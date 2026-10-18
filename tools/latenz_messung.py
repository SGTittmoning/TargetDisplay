"""Misst die Verzoegerung des Kamera-Streams anhand der Uhrzeit im Kamerabild.

Jeder Sekundenwechsel der eingeblendeten Kamera-Uhr markiert einen bekannten
Aufnahmezeitpunkt (volle Sekunde). Die Ankunftszeit des ersten Bildes mit der
neuen Sekunde, gemessen mit der per NTP gestellten Systemuhr, liegt um die
Stream-Verzoegerung dahinter. Der Nachkommaanteil ergibt sich aus vielen
Wechseln statistisch, ganze Sekunden aus den gespeicherten Ausschnitten der
Uhrzeit (Dateiname = Ankunftszeit).

Voraussetzung: Die Kamera-Uhr laeuft per NTP synchron, ihr Versatz geht 1:1
ins Ergebnis ein.

Aufruf ueber latenz_messung.sh (haelt die App waehrend der Messung an).
"""
import argparse
import math
import os
import sys
import time
import types

import cv2
import numpy as np

APP_DIR = '/home/targetdisplay/TargetDisplay'
sys.path.insert(0, APP_DIR)

import camera  # noqa: E402
import settings_store  # noqa: E402
import yamlconfig  # noqa: E402

# Oberer Bildstreifen, in dem die Uhrzeit gesucht wird (Anteil der Bildhoehe).
BAND = 0.12
# Kalibrierphase zum Finden der Sekunden-Ziffer.
CALIB_SEC = 15
# Mindestaenderung eines Pixels (Grauwert) zwischen zwei Bildern.
PIXEL_THRESHOLD = 40
# Anteil veraenderter Pixel, ab dem ein Bild als Ziffernwechsel gilt.
# Rauschen liegt um 0,001, jeder echte Ziffernwechsel deutlich ueber 0,05.
CHANGE_THRESHOLD = 0.03


def open_stream():
    os.chdir(APP_DIR)
    cfg = yamlconfig.load('config.yml')
    stand = settings_store.load_active_stand(settings_store.load_stands(), cfg)
    if stand is None:
        sys.exit('Kein aktiver Stand gefunden.')
    print(f"Stand: {stand.get('displayName')}")
    # Derselbe Verbindungsaufbau wie in der App (Timeouts, Decoder-Threads).
    return camera.Camera._open_container(types.SimpleNamespace(stream_url=stand['url']))


def frames(container):
    for av_frame in container.decode(video=0):
        img = av_frame.to_ndarray(format='bgr24')
        yield time.time(), img


def find_seconds_box(bands):
    # Pixel der Sekunden-Einerstelle aendern sich etwa einmal pro Sekunde,
    # Bildrauschen selten so stark, die Zehnerstelle nur alle 10 s.
    stack = np.stack(bands).astype(np.int16)
    changes = (np.abs(np.diff(stack, axis=0)) > PIXEL_THRESHOLD).sum(axis=0)
    mask = ((changes >= 0.3 * CALIB_SEC) & (changes <= 1.5 * CALIB_SEC)).astype(np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
    ys, xs = np.nonzero(mask)
    if len(xs) < 10:
        return None
    pad = 3
    h, w = mask.shape
    return (max(int(xs.min()) - pad, 0), max(int(ys.min()) - pad, 0),
            min(int(xs.max()) + pad + 1, w), min(int(ys.max()) + pad + 1, h))


def circular_mean(values):
    s = sum(math.sin(2 * math.pi * v) for v in values)
    c = sum(math.cos(2 * math.pi * v) for v in values)
    mean = (math.atan2(s, c) / (2 * math.pi)) % 1.0
    spread = math.sqrt(max(-2 * math.log(max(math.hypot(s, c) / len(values), 1e-9)), 0)) / (2 * math.pi)
    return mean, spread


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dauer', type=int, default=300, help='Messdauer in Sekunden')
    ap.add_argument('--out', required=True)
    ap.add_argument('--box', help='Sekundenziffer x0,y0,x1,y1 (volle Aufloesung), sonst automatisch')
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)

    container = open_stream()
    stream = frames(container)
    t0, first = next(stream)
    height, width = first.shape[:2]
    band_h = int(height * BAND)
    cv2.imwrite(os.path.join(args.out, 'erstes_bild.jpg'), first)
    print(f"Bildgroesse {width}x{height}, Messdauer {args.dauer} s")

    if args.box:
        box = tuple(int(v) for v in args.box.split(','))
    else:
        bands = [cv2.cvtColor(first[:band_h], cv2.COLOR_BGR2GRAY)]
        for t, img in stream:
            bands.append(cv2.cvtColor(img[:band_h], cv2.COLOR_BGR2GRAY))
            if t - t0 >= CALIB_SEC:
                break
        box = find_seconds_box(bands)
        if box is None:
            sys.exit('Sekundenziffer nicht gefunden - bitte erstes_bild.jpg ansehen und --box angeben.')
    x0, y0, x1, y1 = box
    print(f"Sekundenziffer: {x0},{y0},{x1},{y1}")
    preview = first[:band_h].copy()
    cv2.rectangle(preview, (x0, y0), (x1, y1), (0, 0, 255), 2)
    cv2.imwrite(os.path.join(args.out, 'uhr_bereich.png'), preview)

    # Ausschnitt mit der ganzen Uhrzeit zum Ablesen (links von der Ziffer).
    wide = (max(x0 - 12 * (x1 - x0), 0), y0, min(x1 + (x1 - x0), width), y1)

    times, crops, saved = [], [], 0
    start = time.time()
    for t, img in stream:
        times.append(t)
        crops.append(cv2.cvtColor(img[y0:y1, x0:x1], cv2.COLOR_BGR2GRAY))
        if saved < 20 and len(crops) >= 2:
            diff = (np.abs(crops[-1].astype(np.int16) - crops[-2]) > PIXEL_THRESHOLD).mean()
            if diff > CHANGE_THRESHOLD:
                # Dateiname = Ankunftszeit am Pi (Ortszeit, Millisekunden).
                name = time.strftime('uhr_%H-%M-%S', time.localtime(t)) + f'.{int(t % 1 * 1000):03d}.png'
                cv2.imwrite(os.path.join(args.out, name), img[wide[1]:wide[3], wide[0]:wide[2]])
                saved += 1
        if t - start >= args.dauer:
            break
    container.close()

    # Wechsel: Anteil stark veraenderter Pixel gegenueber dem Vorbild.
    score = [0.0] + [float((np.abs(crops[i].astype(np.int16) - crops[i - 1]) > PIXEL_THRESHOLD).mean())
                     for i in range(1, len(crops))]
    events = [times[i] for i in range(1, len(score)) if score[i] > CHANGE_THRESHOLD]
    # Mehrere aufeinanderfolgende Bilder desselben Wechsels nur einmal zaehlen.
    transitions = []
    for t in events:
        if not transitions or t - transitions[-1] > 0.5:
            transitions.append(t)

    duration = times[-1] - times[0]
    fps = (len(times) - 1) / duration if duration > 0 else 0.0
    with open(os.path.join(args.out, 'wechsel.csv'), 'w', encoding='utf-8') as f:
        f.write('ankunft_unix;nachkomma\n')
        for t in transitions:
            f.write(f'{t:.3f};{t % 1:.3f}\n')
    with open(os.path.join(args.out, 'bilder.csv'), 'w', encoding='utf-8') as f:
        f.write('ankunft_unix;score\n')
        for t, s in zip(times, score):
            f.write(f'{t:.3f};{s:.4f}\n')

    print(f"Bilder: {len(times)} in {duration:.0f} s ({fps:.2f} fps)")
    print(f"Sekundenwechsel erkannt: {len(transitions)} (erwartet etwa {duration:.0f})")
    if len(transitions) < 0.8 * duration:
        print('WARNUNG: deutlich weniger Wechsel als Sekunden - Ergebnis unsicher, uhr_bereich.png pruefen.')
    if not transitions:
        return
    mean, spread = circular_mean([t % 1 for t in transitions])
    # Das erste Bild mit der neuen Sekunde wurde im Mittel eine halbe
    # Bildperiode nach dem Wechsel aufgenommen.
    frac = (mean - 0.5 / fps) % 1.0 if fps else mean
    print(f"Nachkommaanteil der Ankunft: Mittel {mean:.2f} s, Streuung {spread:.2f} s")
    print(f"Verzoegerung = N + {frac:.2f} s (N ganze Sekunden, aus den uhr_*.png ablesen:")
    print("  angezeigte Uhrzeit mit der Uhrzeit im Dateinamen vergleichen)")


if __name__ == '__main__':
    main()
