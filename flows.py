"""Dialog-Ablaeufe auf der Oberflaeche: PIN, Stand-Auswahl, Ausschnitt-Editor, Einstellungen."""
import math
import subprocess
import sys
import time
import tkinter as tk

import cv2

import transformlib as tl
import ui
from camera import STREAM_STARTUP_TIMEOUT_SEC
from pinlock import PinLimiter
from settings_store import (
    DEFAULT_PIN,
    save_active_stand,
    save_pin,
    save_sections_override,
)
from ui import EDITOR_MAX_H, EDITOR_MAX_W, FG_DARK, WIN_CLOSED, popup, show_page


def _wait_after_error(error_text, wait_s):
    # Zeigt error_text rot und wartet wait_s Sekunden, ohne die Oberflaeche
    # einzufrieren. Tastendruecke waehrend der Wartezeit werden verworfen.
    # Liefert False, wenn waehrenddessen abgebrochen wurde (Abbrechen/Fenster
    # zu), sonst True.
    end = time.monotonic() + wait_s
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return True
        text = error_text if wait_s < 2 else f'{error_text} {math.ceil(remaining)}s'
        ui.window['-PINDISPLAY-'].update(text, text_color='red')
        event, _ = ui.window.read(timeout=200)
        if event in (WIN_CLOSED, '-PIN_CANCEL-'):
            return False


# Ereignisse der Zifferntasten. Eine Menge statt eines Strings: "in" auf
# einem String traefe auch Teilstrings wie '12'.
PIN_DIGITS = frozenset('0123456789')


def _run_pin_keypad(title, show_cancel, validate):
    # Tastenfeld fuer check_pin() und _enter_new_pin(). validate(entered)
    # liefert bei OK (True, ergebnis) oder (False, (fehlertext, sekunden)); der
    # Fehlertext steht dann so lange rot da (ab 2 s mit Restzeit), danach geht
    # es mit leerer Eingabe weiter. Liefert das Ergebnis oder None bei
    # Abbrechen.
    ui.window['-PIN_TITLE-'].update(title)
    ui.window['-PIN_CANCEL-'].update(visible=show_cancel)
    show_page('-PINVIEW-')
    ui.window['-PINDISPLAY-'].update('', text_color=FG_DARK)
    entered = ''
    result = None
    while True:
        event, _ = ui.window.read()
        if event in (WIN_CLOSED, '-PIN_CANCEL-'):
            break
        elif event == '-PIN_CLEAR-':
            entered = ''
        elif event == '-PIN_OK-':
            ok, value = validate(entered)
            if ok:
                result = value
                break
            else:
                error_text, sleep_s = value
                entered = ''
                if not _wait_after_error(error_text, sleep_s):
                    break
        elif event in PIN_DIGITS:
            if len(entered) < 6:
                entered += event
        ui.window['-PINDISPLAY-'].update('*' * len(entered), text_color=FG_DARK)
    return result


PIN_LIMITER = PinLimiter()


def check_pin(correct_pin):
    # PIN-Abfrage vor den Einstellungen. Nach jedem Fehlversuch eine laenger
    # werdende Pause (pinlock.py): schreckt zufaelliges Herumtippen ab, ist aber
    # kein Schutz gegen gezieltes Durchprobieren.
    def validate(entered):
        if entered == str(correct_pin):
            PIN_LIMITER.register_success()
            return True, True
        return False, ('falsch', PIN_LIMITER.register_failure())
    result = _run_pin_keypad('PIN eingeben', True, validate)
    show_page('-MAINVIEW-')
    return bool(result)


def _reboot():
    # True, wenn der Neustart ausgeloest wurde. Scheitert sudo, bliebe die
    # Standaufsicht sonst ohne Rueckmeldung im Menue.
    try:
        result = subprocess.run(['/usr/bin/sudo', '/usr/sbin/reboot'], check=False)
    except OSError as e:
        print(f"Neustart fehlgeschlagen: {e}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"Neustart fehlgeschlagen: Exit-Code {result.returncode}", file=sys.stderr)
        return False
    return True


def confirm_reboot():
    # Wechselt die Seite nicht selbst; nach Abbrechen entscheidet der Aufrufer,
    # wohin es zurueckgeht.
    show_page('-CONFIRMVIEW-')
    event, _ = ui.window.read()
    return event == '-CONFIRM_YES-'


def validate_new_pin(entered):
    # Prueft eine neue PIN: 4-6 Ziffern und nicht der Standard-PIN (den wuerde
    # main() sofort wieder zur Aenderung vorlegen). Liefert (True, entered) oder
    # (False, (Fehlertext, Wartezeit_s)) wie _run_pin_keypad() erwartet. Das
    # Anzeigefeld fasst rund 11 Zeichen.
    if not 4 <= len(entered) <= 6:
        return False, ('4-6 Ziffern', 0.8)
    if entered == DEFAULT_PIN:
        return False, ('zu einfach', 1.5)
    return True, entered


def _enter_new_pin(title, show_cancel):
    # Liefert die eingetippte Ziffernfolge oder None (Abbrechen), ohne
    # Vergleich mit einer gespeicherten PIN. change_pin_flow() ruft dies fuer
    # Eingabe und Wiederholung auf.
    result = _run_pin_keypad(title, show_cancel, validate_new_pin)
    # Seite fuer die naechste Nutzung (check_pin) wieder in den
    # Grundzustand versetzen.
    ui.window['-PIN_TITLE-'].update('PIN eingeben')
    ui.window['-PIN_CANCEL-'].update(visible=True)
    return result


def change_pin_flow(current_pin, forced):
    # forced=True: Assistent, solange der Standard-PIN gilt; kein Abbrechen.
    # forced=False: Aenderung ueber das Menue, fragt zuerst die aktuelle PIN ab
    # und laesst sich abbrechen.
    if not forced:
        if not check_pin(current_pin):
            return None
    while True:
        new1 = _enter_new_pin('Neuen PIN eingeben (4-6 Ziffern)', show_cancel=not forced)
        if new1 is None:
            return None
        new2 = _enter_new_pin('PIN wiederholen', show_cancel=not forced)
        if new2 is None:
            return None
        if new1 == new2:
            break
        ui.window['-PIN_TITLE-'].update('PINs stimmen nicht überein')
        ui.window.refresh()
        time.sleep(1.0)
    if save_pin(new1):
        return new1
    popup('PIN konnte nicht gespeichert werden.')
    return None


def run_stand_select(stands, forced):
    # forced=True: Assistent, noch kein Stand gewaehlt; kein Abbrechen.
    # forced=False: Stand-Wechsel ueber das Menue, abbrechbar.
    show_page('-STANDVIEW-')
    ui.window['-STAND_BACK-'].update(visible=not forced)
    if not stands:
        # Ohne Stand-Liste (z.B. noch nicht per Ansible verteilt) eine klare
        # Meldung statt einer leeren Auswahl.
        ui.window['-STAND_LIST-'].update(values=['(keine Stand-Liste gefunden - bitte Installation prüfen)'])
        ui.window['-STAND_SELECT-'].update(disabled=True)
    else:
        ui.window['-STAND_LIST-'].update(values=[s['displayName'] for s in stands])
        ui.window['-STAND_SELECT-'].update(disabled=False)
    ui.window.refresh()
    while True:
        event, values = ui.window.read()
        if event == WIN_CLOSED:
            return None
        elif event == '-STAND_BACK-' and not forced:
            return None
        elif event == '-STAND_SELECT-' and stands:
            # Nach Position, nicht nach Name: zwei Staende koennten gleich heissen.
            sel = ui.window['-STAND_LIST-'].widget.curselection()
            if sel:
                return stands[sel[0]]


def wait_for_camera_frame(cap, max_wait_sec=STREAM_STARTUP_TIMEOUT_SEC):
    # Wartet auf den ersten Frame nach der Stand-Auswahl. Die Kamera versucht
    # im Hintergrund laufend neu zu verbinden; diese Seite bleibt dabei
    # bedienbar und kehrt zurueck, sobald ein Frame da ist. Nach
    # max_wait_sec erscheint ein Hinweis, "Erneut versuchen" startet die
    # Wartezeit neu. Gibt den Frame zurueck, oder 'back' fuer die
    # Stand-Auswahl.
    show_page('-CAMWAITVIEW-')

    def start_waiting():
        ui.window['-CAMWAIT_TEXT-'].update('Verbinde mit Kamera...')
        ui.window.refresh()
        return time.monotonic() + max_wait_sec

    deadline = start_waiting()
    timed_out = False
    while True:
        frame = cap.getFrame(full=True)
        if frame is not None:
            return frame
        if not timed_out and time.monotonic() >= deadline:
            timed_out = True
            ui.window['-CAMWAIT_TEXT-'].update('Kamera nicht erreichbar.\nBitte URL/Verkabelung prüfen.')
            ui.window.refresh()
        event, _ = ui.window.read(timeout=300)
        if event in (WIN_CLOSED, '-CAMWAIT_BACK-'):
            return 'back'
        if event == '-CAMWAIT_RETRY-':
            deadline = start_waiting()
            timed_out = False


def edit_section_points(region_label, cap, points, other_points=None, allow_cancel=True, will_restart=True):
    # points: 4 Punkte [x, y] in Kamerakoordinaten des vollen Bilds, oder None
    # (Assistent, noch keine Kalibrierung): dann dient ein zentriertes
    # Rechteck als Startwert. getFrame(full=True) liefert das unbeschnittene
    # Bild, damit sich Ausschnitte auch ueber die bisherigen Grenzen hinaus
    # setzen lassen. Liefert beim Speichern die neuen Punkte, sonst None.
    #
    # will_restart bestimmt nur die Beschriftung des Speichern-Buttons: aus dem
    # Menue startet die App nach dem Speichern neu ("Speichern und neu
    # starten"), im Assistenten laeuft sie weiter ("Speichern").
    frame = cap.getFrame(full=True)
    if frame is None:
        popup('Kein Kamerabild verfügbar - bitte später erneut versuchen.')
        return None

    img_h, img_w = frame.shape[:2]
    if points is None:
        mx, my = int(img_w * 0.2), int(img_h * 0.2)
        points = [[mx, my], [img_w - mx, my], [mx, img_h - my], [img_w - mx, img_h - my]]
    max_disp_w, max_disp_h = EDITOR_MAX_W, EDITOR_MAX_H
    scale = min(max_disp_w / img_w, max_disp_h / img_h, 1.0)
    disp_w, disp_h = max(1, int(img_w * scale)), max(1, int(img_h * scale))
    # Der Canvas hat eine feste Groesse (EDITOR_MAX_W x EDITOR_MAX_H). Das
    # skalierte Bild wird darin zentriert (off_x/off_y).
    off_x, off_y = (EDITOR_MAX_W - disp_w) // 2, (EDITOR_MAX_H - disp_h) // 2

    HIT_RADIUS = 35   # grosszuegiger Trefferbereich fuer Finger, in Display-Pixeln
    MAG_SIZE = 220    # Seitenlaenge des Lupen-Overlays in Pixeln
    MAG_SRC = 70       # Seitenlaenge des vergroesserten Kamera-Ausschnitts (Quelle)

    def to_display_frame(f):
        return cv2.resize(f, (disp_w, disp_h)) if scale != 1.0 else f.copy()

    disp_frame = to_display_frame(frame)
    # Ab hier sind pts Canvas-Koordinaten (Skalierung und Versatz
    # eingerechnet), wie event.x/event.y beim Ziehen.
    pts = [[p[0] * scale + off_x, p[1] * scale + off_y] for p in points]
    # Der andere Ausschnitt wird zur Orientierung hellgrau mitgezeichnet,
    # nicht verschiebbar.
    other_pts = None
    if other_points is not None:
        other_pts = [[p[0] * scale + off_x, p[1] * scale + off_y] for p in other_points]

    show_page('-EDITORVIEW-')
    ui.window['-EDITOR_TITLE-'].update(f'{region_label}: Eckpunkte anpassen')
    ui.window['-EDIT_CANCEL-'].update(visible=allow_cancel)
    # Zweizeilig, sonst wird der Button zu breit.
    ui.window['-EDIT_SAVE-'].update('Speichern und\nneu starten' if will_restart else 'Speichern')
    graph = ui.window.editor_canvas

    def draw_magnifier(center_disp):
        # Am Bildrand wird der fehlende Teil per BORDER_REPLICATE aufgefuellt.
        # So bleibt der Ausschnitt MAG_SRC x MAG_SRC und das Fadenkreuz auf dem
        # Punkt; ein Beschneiden ergaebe einen verzerrten Ausschnitt.
        cx, cy = (center_disp[0] - off_x) / scale, (center_disp[1] - off_y) / scale
        half = MAG_SRC // 2
        x0, y0 = int(cx - half), int(cy - half)
        x1, y1 = x0 + MAG_SRC, y0 + MAG_SRC
        src_x0, src_y0 = max(0, x0), max(0, y0)
        src_x1, src_y1 = min(img_w, x1), min(img_h, y1)
        crop = frame[src_y0:src_y1, src_x0:src_x1]
        if crop.size == 0:
            return
        pad_left, pad_top = src_x0 - x0, src_y0 - y0
        pad_right, pad_bottom = x1 - src_x1, y1 - src_y1
        if pad_left or pad_top or pad_right or pad_bottom:
            crop = cv2.copyMakeBorder(crop, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_REPLICATE)
        mag = cv2.resize(crop, (MAG_SIZE, MAG_SIZE), interpolation=cv2.INTER_NEAREST)
        cv2.line(mag, (MAG_SIZE // 2, 0), (MAG_SIZE // 2, MAG_SIZE), (0, 255, 255), 1)
        cv2.line(mag, (0, MAG_SIZE // 2), (MAG_SIZE, MAG_SIZE // 2), (0, 255, 255), 1)
        # PPM statt PNG: unkomprimiert ist billiger, das Bild wird sofort wieder
        # dekodiert. Die Lupe wird bei jeder Zugbewegung neu gezeichnet.
        magbytes = cv2.imencode('.ppm', mag)[1].tobytes()
        # Die Lupe sitzt in der Canvas-Ecke, die vom gezogenen Punkt am weitesten
        # entfernt ist, damit sie ihn nicht verdeckt. Sie wechselt die Ecke, sobald
        # der Punkt die Mittellinie ueberquert.
        mag_margin = 10
        mag_x = mag_margin if center_disp[0] >= EDITOR_MAX_W / 2 else EDITOR_MAX_W - MAG_SIZE - mag_margin
        mag_y = mag_margin if center_disp[1] >= EDITOR_MAX_H / 2 else EDITOR_MAX_H - MAG_SIZE - mag_margin
        photo = tk.PhotoImage(data=magbytes)
        graph.image_refs.append(photo)
        graph.create_image(mag_x, mag_y, anchor='nw', image=photo)
        graph.create_rectangle(mag_x, mag_y, mag_x + MAG_SIZE, mag_y + MAG_SIZE, outline='yellow', width=2)

    def draw_outline(poly_pts, color):
        # Die Punkte haben keine feste Umlaufrichtung; 1-2-3-4-1 verbunden ergaebe
        # evtl. ein ueberkreuztes Viereck. Fuer den Umriss werden sie deshalb nach
        # Winkel um ihren Mittelpunkt sortiert, ihre Nummerierung bleibt.
        cx = sum(p[0] for p in poly_pts) / 4
        cy = sum(p[1] for p in poly_pts) / 4
        perimeter = sorted(range(4), key=lambda i: math.atan2(poly_pts[i][1] - cy, poly_pts[i][0] - cx))
        for j in range(4):
            a, b = poly_pts[perimeter[j]], poly_pts[perimeter[(j + 1) % 4]]
            graph.create_line(a[0], a[1], b[0], b[1], fill=color, width=2)

    def redraw(mag_center=None):
        graph.delete('all')
        graph.image_refs = []
        photo = tk.PhotoImage(data=cv2.imencode('.ppm', disp_frame)[1].tobytes())  # PPM statt PNG, s.o.
        graph.image_refs.append(photo)
        graph.create_image(off_x, off_y, anchor='nw', image=photo)
        if other_pts is not None:
            draw_outline(other_pts, '#c0c0c0')
        draw_outline(pts, 'yellow')
        for i, p in enumerate(pts):
            graph.create_oval(p[0] - 10, p[1] - 10, p[0] + 10, p[1] + 10,
                               fill='red', outline='yellow', width=2)
            graph.create_text(p[0], p[1] - 20, text=str(i + 1), fill='yellow',
                               font=('Helvetica', 12, 'bold'))
        if mag_center is not None:
            draw_magnifier(mag_center)

    redraw()
    dragging_idx = None

    while True:
        event, values = ui.window.read()
        if event in (WIN_CLOSED, '-EDIT_CANCEL-'):
            return None
        elif event == '-EDIT_REFRESH-':
            new_frame = cap.getFrame(full=True)
            if new_frame is not None:
                frame = new_frame
                disp_frame = to_display_frame(frame)
                redraw()
        elif event == '-EDIT_SAVE-':
            result = [[(p[0] - off_x) / scale, (p[1] - off_y) / scale] for p in pts]
            # Ein ueberkreuztes, nicht konvexes oder zu kleines Viereck ergaebe
            # eine unbrauchbare Perspektiv-Entzerrung (leeres/verzerrtes Bild).
            if not tl.is_valid_quad(result):
                popup('Die vier Punkte bilden kein gültiges Viereck.\nBitte die Ecken so setzen, dass ein Rechteck bzw. Trapez entsteht.')
                continue
            return result
        elif event == '-EDITGRAPH-':
            pos = values['-EDITGRAPH-']
            if pos is None:
                continue
            if dragging_idx is None:
                best_i, best_d = None, HIT_RADIUS
                for i, p in enumerate(pts):
                    d = ((p[0] - pos[0]) ** 2 + (p[1] - pos[1]) ** 2) ** 0.5
                    if d < best_d:
                        best_i, best_d = i, d
                dragging_idx = best_i
            if dragging_idx is not None:
                # Auf den sichtbaren Bildbereich begrenzt; ausserhalb liegt nur der
                # schwarze Rand ohne Bildkoordinate.
                pts[dragging_idx] = [
                    min(max(pos[0], off_x), off_x + disp_w),
                    min(max(pos[1], off_y), off_y + disp_h),
                ]
                redraw(mag_center=pts[dragging_idx])
        elif event == '-EDITGRAPH-+UP':
            dragging_idx = None
            redraw()


def run_settings_flow(cap, section_full, section_detail, stands, current_pin):
    # Einstellungen-Menue. section_full/section_detail sind Kamerakoordinaten
    # des vollen Bilds, wie sie gespeichert werden. Liefert True, wenn etwas
    # gespeichert wurde (Ausschnitte, Stand oder PIN); main() beendet sich dann
    # und systemd startet mit den neuen Werten neu. Auch die PIN laeuft ueber
    # diesen Neustart: ein Weg fuer alles ist weniger fehleranfaellig.
    # Abbrechen oder ein Fehler in einer Unteraktion fuehrt zurueck in dieses
    # Menue, nicht zum Hauptbildschirm.
    while True:
        show_page('-MENUVIEW-')
        event, _ = ui.window.read()
        if event in (WIN_CLOSED, '-MENU_BACK-'):
            show_page('-MAINVIEW-')
            return False
        elif event in ('-MENU_FULL-', '-MENU_DETAIL-'):
            which = 'full' if event == '-MENU_FULL-' else 'detail'
            current = section_full if which == 'full' else section_detail
            other = section_detail if which == 'full' else section_full
            result = edit_section_points('Ganze Scheibe' if which == 'full' else 'Innen Scheibe', cap, current, other_points=other)
            if result is None:
                continue
            new_section_full = result if which == 'full' else section_full
            new_section_detail = result if which == 'detail' else section_detail
            if not save_sections_override(new_section_full, new_section_detail):
                popup('Speichern fehlgeschlagen - Änderung wurde NICHT übernommen.')
                continue
            show_page('-MAINVIEW-')
            return True
        elif event == '-MENU_STAND-':
            chosen = run_stand_select(stands, forced=False)
            if chosen is None:
                continue
            if not save_active_stand(chosen['id']):
                popup('Stand konnte nicht gespeichert werden.')
                continue
            show_page('-MAINVIEW-')
            return True
        elif event == '-MENU_PIN-':
            new_pin = change_pin_flow(current_pin, forced=False)
            if new_pin is None:
                continue
            show_page('-MAINVIEW-')
            return True
        elif event == '-MENU_RESTART-':
            # Die PIN-Abfrage vor dem Menue schuetzt auch den Neustart.
            if confirm_reboot() and not _reboot():
                popup('Neustart fehlgeschlagen.')
            continue
