"""Dialog-Ablaeufe auf der Oberflaeche: PIN, Stand-Auswahl, Ausschnitt-Editor, Einstellungen."""
import math
import subprocess
import time
import tkinter as tk

import cv2

import transformlib as tl
import ui
from camera import STREAM_STARTUP_TIMEOUT_SEC
from pinlock import PinLimiter
from settings_store import save_active_stand, save_pin, save_sections_override
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


def _run_pin_keypad(title, show_cancel, validate):
    # Gemeinsames Tastenfeld-Grundgeruest fuer check_pin() und
    # _enter_new_pin() - beide sammeln Ziffern/-PIN_CLEAR- identisch und
    # unterscheiden sich nur darin, was bei -PIN_OK- als "gueltig" zaehlt
    # und was bei Erfolg zurueckgegeben wird. validate(entered) liefert
    # (True, ergebnis) bei Erfolg (Schleife endet), sonst
    # (False, (fehlertext, sekunden)) - zeigt den Fehlertext rot fuer die
    # angegebene Dauer (ab 2 s mit Restzeit; Eingaben waehrenddessen werden
    # verworfen, Abbrechen bleibt moeglich), dann geht die Eingabe leer
    # weiter. Liefert das Erfolgsergebnis, oder None bei Abbrechen/Fenster zu.
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
        elif event in '0123456789':
            if len(entered) < 6:
                entered += event
        ui.window['-PINDISPLAY-'].update('*' * len(entered), text_color=FG_DARK)
    return result


PIN_LIMITER = PinLimiter()


def check_pin(correct_pin):
    # Generische PIN-Abfrage, schuetzt sowohl Settings als auch Restart.
    # Keine Sperre, aber nach jedem Fehlversuch eine laenger werdende Pause
    # (siehe pinlock.py) - das Bedrohungsmodell ist "zufaelliges Herumtippen
    # vor Ort abschrecken", keine gezielte Brute-Force-Absicherung.
    # Titel/Abbrechen-Sichtbarkeit werden von _run_pin_keypad() explizit
    # auf 'PIN eingeben'/sichtbar zurueckgesetzt - _enter_new_pin()
    # (PIN-Aenderung) aendert beides auf derselben Seite, eine vorherige
    # Aenderung darf hier nicht durchschlagen.
    def validate(entered):
        if entered == str(correct_pin):
            PIN_LIMITER.register_success()
            return True, True
        return False, ('falsch', PIN_LIMITER.register_failure())
    result = _run_pin_keypad('PIN eingeben', True, validate)
    show_page('-MAINVIEW-')
    return bool(result)


def confirm_reboot():
    # Navigiert bewusst NICHT selbst weiter - das entscheidet der Aufrufer,
    # der je nach Kontext nach einem Abbruch zurueck zum Settings-Menue statt
    # zur Hauptseite will (siehe run_settings_flow(): Abbrechen geht dort nur
    # einen Schritt zurueck, nicht bis zur Hauptseite).
    show_page('-CONFIRMVIEW-')
    event, _ = ui.window.read()
    return event == '-CONFIRM_YES-'


def _enter_new_pin(title, show_cancel):
    # Liefert die eingetippte Ziffernfolge (4-6 Stellen, OK gedrueckt)
    # zurueck, oder None (Abbrechen/Fenster zu) - OHNE Vergleich mit einem
    # "richtigen" PIN, anders als check_pin(). Wird sowohl fuer die
    # Neueingabe als auch die Wiederholung genutzt (change_pin_flow ruft
    # diese Funktion zweimal auf).
    def validate(entered):
        if 4 <= len(entered) <= 6:
            return True, entered
        return False, ('4-6 Ziffern', 0.8)
    result = _run_pin_keypad(title, show_cancel, validate)
    # Seite fuer die naechste Nutzung (check_pin) wieder in den
    # Grundzustand versetzen.
    ui.window['-PIN_TITLE-'].update('PIN eingeben')
    ui.window['-PIN_CANCEL-'].update(visible=True)
    return result


def change_pin_flow(current_pin, forced):
    # forced=True: Ersteinrichtungs-Assistent, solange der PIN noch auf dem
    # Standardwert DEFAULT_PIN steht - kein Abbrechen moeglich (Cancel-
    # Button versteckt), es gibt ja noch nichts zu schuetzen. forced=False:
    # freiwillige Aenderung ueber das Settings-Menue - verlangt zuerst den
    # AKTUELLEN PIN zur Bestaetigung (wie ein normaler "Passwort aendern"-
    # Dialog), abbrechbar.
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
    # forced=True: Ersteinrichtungs-Assistent, noch kein Stand ausgewaehlt -
    # kein Zurueck moeglich (der Hauptbildschirm ist ja noch nicht
    # erreichbar). forced=False: freiwilliger Stand-Wechsel ueber das
    # Settings-Menue, abbrechbar.
    show_page('-STANDVIEW-')
    ui.window['-STAND_BACK-'].update(visible=not forced)
    if not stands:
        # Keine Stand-Liste vorhanden (z.B. Ansible-Deploy hat sie noch
        # nicht gebracht) - klare Meldung statt einer leeren, ratlosen
        # Auswahlliste.
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
            # curselection() statt Werteabgleich per Name - robust auch
            # falls zwei Staende zufaellig denselben Anzeigenamen haben.
            sel = ui.window['-STAND_LIST-'].widget.curselection()
            if sel:
                return stands[sel[0]]


def wait_for_camera_frame(cap, max_wait_sec=STREAM_STARTUP_TIMEOUT_SEC):
    # Gnadenfrist fuer den allerersten Frame direkt nach der Stand-Auswahl -
    # dieselbe Grosszuegigkeit wie STREAM_STARTUP_TIMEOUT_SEC (av.open() hat
    # keinen expliziten Verbindungs-Timeout, ein frischer Connect kann
    # vereinzelt 30s+ dauern, siehe Camera.is_stale-Kommentar; live am
    # Test-Pi hat allein der Container-Open schon 8s gebraucht). Anders als
    # ein erster Entwurf (blockierendes time.sleep() bis zum Ablauf, DANACH
    # erst eine interaktive Seite) pollt diese Version durchgehend ueber
    # window.read(timeout=...) - das Fenster bleibt die ganze Wartezeit über
    # reaktionsfaehig (Zurueck-Knopf funktioniert sofort, kein bis zu 30s
    # eingefroren wirkender Bildschirm) UND erkennt eine zwischenzeitlich
    # doch noch erfolgreiche Verbindung automatisch, ganz ohne Klick auf
    # "Erneut versuchen". Gibt den Frame zurueck, oder den String 'back'
    # wenn der Nutzer zur Stand-Auswahl zurueck moechte.
    show_page('-CAMWAITVIEW-')
    ui.window['-CAMWAIT_TEXT-'].update('Verbinde mit Kamera...')
    ui.window.refresh()
    deadline = time.monotonic() + max_wait_sec
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


def edit_section_points(region_label, cap, points, other_points=None, allow_cancel=True, will_restart=True):
    # points: Liste von 4 [x,y] in ORIGINALEN Kamerakoordinaten (nicht
    # Crop-verschoben), oder None falls noch keine Kalibrierung existiert
    # (Ersteinrichtungs-Assistent) - dann wird unten ein zentriertes
    # Rechteck als Startwert gesetzt, von dem aus die Ecken manuell in
    # Position gezogen werden. cap.getFrame(full=True) liefert bewusst das
    # unbeschnittene Kamerabild (siehe camera.py) - der normale Anzeige-Crop
    # ist eng um die AKTUELLEN section_full/section_detail-Grenzen gelegt,
    # der Editor zum NEU-Setzen der Ausschnitte muss aber auch Bereiche
    # ausserhalb dieser Grenzen zeigen koennen. Gibt die neue Punkteliste
    # (gleicher, originaler Koordinatenraum) beim Speichern zurueck, sonst None.
    #
    # will_restart steuert nur die Beschriftung des Speichern-Buttons: der
    # freiwillige Settings-Menue-Pfad (run_settings_flow) beendet main.py nach
    # dem Speichern IMMER per sys.exit(0) (Restart=always startet mit den
    # neuen Werten neu), der erzwungene Ersteinrichtungs-Assistent dagegen
    # laeuft nach dem Speichern eines einzelnen Ausschnitts einfach im selben
    # Prozess weiter (siehe main()) - ohne diese Unterscheidung waere
    # "Speichern" im Settings-Kontext irrefuehrend, weil das Geraet danach
    # unvermittelt kurz schwarz wird und neu startet.
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
    # Der Editor-Canvas hat eine feste Groesse (EDITOR_MAX_W x EDITOR_MAX_H -
    # das Layout wird nur einmal beim Programmstart gebaut), das
    # tatsaechliche Kamerabild passt je nach Seitenverhaeltnis meist nicht
    # exakt hinein. off_x/off_y zentrieren das skalierte Bild in diesem
    # Canvas, statt es oben links kleben zu lassen (das wuerde sonst einen
    # einseitigen schwarzen Rand rechts erzeugen).
    off_x, off_y = (EDITOR_MAX_W - disp_w) // 2, (EDITOR_MAX_H - disp_h) // 2

    HIT_RADIUS = 35   # grosszuegiger Trefferbereich fuer Finger, in Display-Pixeln
    MAG_SIZE = 220    # Seitenlaenge des Lupen-Overlays in Pixeln
    MAG_SRC = 70       # Seitenlaenge des vergroesserten Kamera-Ausschnitts (Quelle)

    def to_display_frame(f):
        return cv2.resize(f, (disp_w, disp_h)) if scale != 1.0 else f.copy()

    disp_frame = to_display_frame(frame)
    # pts leben ab hier durchgehend in Canvas-Pixelkoordinaten (Bild-
    # Skalierung UND Zentrierungs-Offset bereits eingerechnet) - das
    # entspricht direkt dem Koordinatenraum, den das Canvas-Widget fuer
    # Klicks liefert (event.x/event.y), dadurch ist beim Dragging keine
    # weitere Umrechnung noetig.
    pts = [[p[0] * scale + off_x, p[1] * scale + off_y] for p in points]
    # Der jeweils ANDERE Ausschnitt (z.B. section_detail waehrend
    # section_full bearbeitet wird) wird nur informativ in hellgrau
    # mitgezeichnet, damit man beim Setzen der Punkte sieht wie sich beide
    # Bereiche zueinander verhalten - rein statisch, nicht klickbar/ziehbar.
    other_pts = None
    if other_points is not None:
        other_pts = [[p[0] * scale + off_x, p[1] * scale + off_y] for p in other_points]

    show_page('-EDITORVIEW-')
    ui.window['-EDITOR_TITLE-'].update(f'{region_label}: Eckpunkte anpassen')
    ui.window['-EDIT_CANCEL-'].update(visible=allow_cancel)
    # Zweizeilig statt eine lange Zeile - "Speichern und neu starten" in
    # einer Zeile wuerde den Button unschoen breit machen.
    ui.window['-EDIT_SAVE-'].update('Speichern und\nneu starten' if will_restart else 'Speichern')
    graph = ui.window.editor_canvas

    def draw_magnifier(center_disp):
        # Punkt kann bis an den Bildrand/in die Ecke gezogen werden - ein
        # einfaches Clamping des Quellausschnitts wuerde dort ein
        # asymmetrisches, verzerrtes Rechteck ergeben (cv2.resize wuerde es
        # verzerrt auf ein Quadrat aufziehen) UND das Fadenkreuz saesse
        # nicht mehr auf dem tatsaechlichen Punkt. Stattdessen wird der
        # fehlende Rand per BORDER_REPLICATE aufgefuellt, der Ausschnitt
        # bleibt so immer MAG_SRC x MAG_SRC und zentriert auf dem Punkt.
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
        # PPM statt PNG: gleiche Begruendung wie bei Window.draw_image() -
        # unkomprimiert ist beim Kodieren billiger, das Ergebnis wird
        # ohnehin sofort wieder dekodiert (kein Speichern/Uebertragen). Der
        # Lupen-Redraw feuert bei jedem Drag-Motion-Event, potenziell
        # mehrfach pro Sekunde.
        magbytes = cv2.imencode('.ppm', mag)[1].tobytes()
        # In der Canvas-Ecke verankert, die vom gerade gezogenen Punkt am
        # weitesten entfernt ist (nicht am Bild, damit die Position
        # unabhaengig von Bildgroesse/Zentrierung vorhersehbar bleibt): eine
        # fest verankerte Lupe wuerde in ihrer eigenen Ecke liegende Punkte
        # beim Platzieren/Verschieben verdecken. Die Lupe springt deshalb in
        # die jeweils gegenueberliegende Ecke, sobald der Punkt die
        # Bildschirm-Mittellinie in X- oder Y-Richtung ueberquert.
        mag_margin = 10
        mag_x = mag_margin if center_disp[0] >= EDITOR_MAX_W / 2 else EDITOR_MAX_W - MAG_SIZE - mag_margin
        mag_y = mag_margin if center_disp[1] >= EDITOR_MAX_H / 2 else EDITOR_MAX_H - MAG_SIZE - mag_margin
        photo = tk.PhotoImage(data=magbytes)
        graph.image_refs.append(photo)
        graph.create_image(mag_x, mag_y, anchor='nw', image=photo)
        graph.create_rectangle(mag_x, mag_y, mag_x + MAG_SIZE, mag_y + MAG_SIZE, outline='yellow', width=2)

    def draw_outline(poly_pts, color):
        # Die Reihenfolge der Punkte in section_full/section_detail folgt
        # keiner festen Umlauf-Konvention (nicht zwingend im/gegen den
        # Uhrzeigersinn) - ein direktes Verbinden 1-2-3-4-1 in dieser
        # Reihenfolge kann daher ein sich selbst ueberschneidendes Viereck
        # ("Bowtie") ergeben. Fuer den Verbindungs-Umriss werden die Punkte
        # deshalb separat nach Winkel
        # um ihren Mittelpunkt sortiert (reiner Anzeige-Zweck) - die
        # Nummerierung/Zuordnung 1-4 der Punkte selbst bleibt unveraendert.
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
                # Auf den tatsaechlich sichtbaren Bildbereich begrenzt (nicht
                # den ganzen Canvas) - ausserhalb liegt nur der zentrierte
                # schwarze Rand, ohne zugehoerige Bildkoordinate.
                pts[dragging_idx] = [
                    min(max(pos[0], off_x), off_x + disp_w),
                    min(max(pos[1], off_y), off_y + disp_h),
                ]
                redraw(mag_center=pts[dragging_idx])
        elif event == '-EDITGRAPH-+UP':
            dragging_idx = None
            redraw()


def run_settings_flow(cap, section_full, section_detail, stands, current_pin):
    # Fuehrt Auswahl + jeweilige Aktion durch. section_full/section_detail
    # sind die ORIGINALEN Koordinaten, wie sie aus config.yml/Override
    # kommen und auch wieder dorthin geschrieben werden -
    # edit_section_points() arbeitet jetzt direkt in diesem Koordinatenraum
    # (zeigt via cap.getFrame(full=True) das unbeschnittene Kamerabild,
    # keine Crop-Offset-Umrechnung mehr noetig, siehe dort). Gibt True
    # zurueck wenn irgendetwas gespeichert wurde, das einen neu berechneten
    # Zustand braucht - main.py beendet sich dann (sys.exit(0)),
    # Restart=always im systemd-Unit laedt alles sauber neu (siehe
    # Aufrufer). Gilt einheitlich fuer Ausschnitte, Stand-Wechsel UND
    # PIN-Aenderung - bewusst kein Sonderfall fuer den PIN, der zwar
    # technisch keinen Neustart braeuchte, aber ein einheitlicher Ablauf
    # ist weniger fehleranfaellig als eine zweite, live-aktualisierte
    # Variable an mehreren Stellen mitzupflegen.
    # Jede Unteraktion (Ausschnitte/Stand/PIN/Restart) kehrt bei Abbruch oder
    # einem Fehlschlag per "continue" zurueck zum Settings-Menue, statt bis
    # zur Hauptseite durchzureichen - ein Abbruch soll nur einen Schritt
    # zurueckgehen, nicht bis zur Hauptseite springen. Nur ein tatsaechlicher
    # '-MENU_BACK-' auf dieser Seite
    # selbst, oder eine ERFOLGREICH gespeicherte Aenderung (die ohnehin einen
    # Neustart ausloest, siehe Aufrufer), verlaesst die Schleife.
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
            # Restart lebt bewusst hier im Menue statt als eigener
            # Hauptbildschirm-Button (siehe _build_menu_view) - der Zugang
            # ist bereits durch den vorgelagerten check_pin() in main()s
            # '-SETTINGS-'-Handler geschuetzt, keine zweite PIN-Abfrage noetig.
            if confirm_reboot():
                subprocess.run(['/usr/bin/sudo', '/usr/sbin/reboot'])
            continue
