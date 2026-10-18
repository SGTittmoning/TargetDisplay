import cv2
import numpy as np

# Farben (BGR, wie OpenCV).
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
GRAY = (200, 200, 200)
RED = (0, 0, 255)
GREEN = (0, 255, 0)


def blend_logo_centered(canvas, logo_rgba, margin_ratio=0.05):
    # Skaliert ein BGRA-Logo unter Beibehaltung des Seitenverhaeltnisses so
    # gross wie moeglich (minus Rand) und zeichnet es alpha-transparent
    # zentriert auf den Canvas.
    if logo_rgba.ndim == 2 or logo_rgba.shape[2] == 1:
        # Ein reines Graustufen-Logo liefert cv2.imread() ohne Farbkanaele.
        logo_rgba = cv2.cvtColor(logo_rgba, cv2.COLOR_GRAY2BGR)
    canvas_h, canvas_w = canvas.shape[:2]
    margin = int(min(canvas_w, canvas_h) * margin_ratio)
    max_w = canvas_w - 2 * margin
    max_h = canvas_h - 2 * margin
    logo_h, logo_w = logo_rgba.shape[:2]
    scale = min(max_w / logo_w, max_h / logo_h)
    new_w, new_h = int(logo_w * scale), int(logo_h * scale)
    logo_rgba = cv2.resize(logo_rgba, (new_w, new_h))
    x0 = (canvas_w - new_w) // 2
    y0 = (canvas_h - new_h) // 2
    if logo_rgba.shape[2] == 4:
        alpha = logo_rgba[:, :, 3:4].astype(np.float32) / 255.0
        logo_bgr = logo_rgba[:, :, :3].astype(np.float32)
        roi = canvas[y0:y0+new_h, x0:x0+new_w].astype(np.float32)
        canvas[y0:y0+new_h, x0:x0+new_w] = (alpha * logo_bgr + (1 - alpha) * roi).astype(np.uint8)
    else:
        canvas[y0:y0+new_h, x0:x0+new_w] = logo_rgba[:, :, :3]
    return canvas


def put_centered(frame, text, font_scale, thickness, color):
    # Text mittig ins Bild. getTextSize() liefert die tatsaechliche Groesse
    # des gerenderten Textes, die Mitte stimmt also unabhaengig von dessen
    # Laenge.
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    x = (frame.shape[1] - tw) // 2
    y = (frame.shape[0] + th) // 2
    cv2.putText(frame, text, (x, y), font, font_scale, color, thickness, cv2.LINE_AA)


def draw_timer_countdown(frame, seconds_left):
    put_centered(frame, str(seconds_left), 6.5, 11, BLACK)


def timer_frame(size, state):
    # Vollbild eines Timer-Zustands (timerlib.TimerState): gruen oder rot,
    # oben links die Nummer des Durchgangs, in der Mitte der Countdown.
    # numpy erwartet (Hoehe, Breite), size ist (Breite, Hoehe).
    frame = np.zeros((size[1], size[0], 3), np.uint8)
    frame[:] = GREEN if state.color == 'green' else RED
    if state.number is not None:
        cv2.putText(frame, str(state.number), (20, 130), cv2.FONT_HERSHEY_SIMPLEX, 5, BLACK, 10, cv2.LINE_AA)
    if state.countdown is not None:
        draw_timer_countdown(frame, state.countdown)
    return frame


def blank_frame(size, logo):
    # "Video aus": schwarz, mit dem Logo als Wasserzeichen, falls vorhanden.
    frame = np.zeros((size[1], size[0], 3), np.uint8)
    return blend_logo_centered(frame, logo) if logo is not None else frame


# Kennfarbe des Replays (BGR): violett, um nicht mit den Timer-Farben
# Gruen/Rot oder den Akzentfarben der Bediengruppen verwechselt zu werden.
REPLAY_COLOR = (179, 75, 123)
REPLAY_BORDER = 10


def _label(frame, text, anchor_right, font_scale=0.9, thickness=2):
    font = cv2.FONT_HERSHEY_SIMPLEX
    (tw, th), base = cv2.getTextSize(text, font, font_scale, thickness)
    pad = 8
    y0 = REPLAY_BORDER
    x0 = frame.shape[1] - REPLAY_BORDER - tw - 2 * pad if anchor_right else REPLAY_BORDER
    cv2.rectangle(frame, (x0, y0), (x0 + tw + 2 * pad, y0 + th + base + 2 * pad), REPLAY_COLOR, -1)
    cv2.putText(frame, text, (x0 + pad, y0 + pad + th), font, font_scale, WHITE, thickness, cv2.LINE_AA)


def draw_replay_marker(frame, counter):
    # Rahmen, "REPLAY" oben links und der Zaehler oben rechts. Wird nach dem
    # Zoom gezeichnet und bleibt deshalb bei jeder Zoomstufe gleich gross.
    h, w = frame.shape[:2]
    cv2.rectangle(frame, (0, 0), (w - 1, h - 1), REPLAY_COLOR, 2 * REPLAY_BORDER)
    _label(frame, 'REPLAY', anchor_right=False)
    _label(frame, counter, anchor_right=True)
    return frame


def replay_separator(size, text):
    # Rotes Trennbild zwischen zwei Durchgaengen, Schrift wie beim Timer.
    # Ohne Text: rotes Schlussbild am Ende des Replays.
    frame = np.zeros((size[1], size[0], 3), np.uint8)
    frame[:] = RED
    if text:
        put_centered(frame, text, 2, 5, BLACK)
    return frame


def replay_no_frame(size):
    # Durchgang ohne Kamerabild (Stream war waehrend der Aufnahme weg).
    frame = np.zeros((size[1], size[0], 3), np.uint8)
    put_centered(frame, 'kein Bild', 1.5, 3, GRAY)
    return frame
