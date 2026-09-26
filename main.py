import os
import signal
import sys
import time
import traceback
from collections import deque
from datetime import datetime

import config_with_yaml as config
import cv2
import numpy as np

import timerlib
import transformlib as tl
import ui
from camera import STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC, Camera
from flows import (
    change_pin_flow,
    check_pin,
    edit_section_points,
    run_settings_flow,
    run_stand_select,
    wait_for_camera_frame,
)
from overlays import blend_logo_centered, draw_timer_countdown
from settings_store import (
    DEFAULT_PIN,
    load_active_stand,
    load_sections,
    load_settings_pin,
    load_stands,
    save_active_stand,
    save_sections_override,
)
from transformlib import VIDEO_ZOOM_FACTOR, clamp_zoom_center
from ui import (
    WIN_CLOSED,
    blink_disabled,
    set_icon_buttons,
    set_stand_name,
    show_page,
    sync_reset_button,
    timer_disabled,
    video_filter_disabled,
    zoom_disabled,
)
from watchdog import stale_check_due


# Ohne einen expliziten Handler ist im Journal nicht sichtbar, ob/wann
# main.py ein SIGTERM (z.B. von "systemctl restart") ueberhaupt erreicht.
# Reagiert main.py nicht rechtzeitig, greift systemd nach TimeoutStopSec
# per SIGKILL ein - das zaehlt als eigener Fehlerzustand ("Failed with
# result 'timeout'") und loest OnFailure=/den Reboot-Guard aus, unabhaengig
# von der Staleness-Logik oben.
def _handle_sigterm(signum, frame):
    print("SIGTERM empfangen, beende main.py.", file=sys.stderr)
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)

# Unbehandelte Exception -> Traceback ins Journal und Exit-Code 3. Python
# selbst wuerde mit Exit-Code 1 enden, den targetdisplay.service
# (SuccessExitStatus=1) als regulaeren Stop wertet - ein Absturz waere dann
# nicht von einem "systemctl stop" zu unterscheiden. os._exit, weil ein
# Exit-Code aus dem excepthook heraus sonst nicht durchschlaegt.
def _handle_uncaught(exc_type, exc_value, exc_tb):
    traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.stderr.flush()
    os._exit(3)

sys.excepthook = _handle_uncaught

# Takt der Hauptschleife (Neuzeichnen, Timer, Uhr). Tasteneingaben wecken
# window.read() sofort, der Takt bestimmt nur, wie schnell ein neuer
# Kamera-Frame (ca. 6 pro Sekunde) oder ein Timer-Schritt auf dem Bildschirm
# erscheint.
MAIN_LOOP_TICK_MS = 33

version = '0.11.2'

cfg = config.load("config.yml")

def main():
    VideoSize = (cfg.getProperty('video.size.x'), cfg.getProperty('video.size.y'))

    frame_count = 1
    frame_timestamps = deque()
    FPS_WINDOW_SECONDS = 60
    displayVideo = True
    displayTimer = False
    timerStart = time.monotonic()
    last_timer_state = None
    timerType = ""
    video_resumed_at = time.monotonic()
    blink = False
    blink_ref = []
    zoom_center = []
    zoom_level = 'full'
    last_frame_id = -1
    last_date_str = None
    last_time_str = None

    window = ui.window = ui.Window(cfg, VideoSize, version)

    #some speed optimisation - avoid searching every frame
    window_date = window['-DATE-']
    window_time = window['-TIME-']
    window_fps = window['-FPS-']

    show_page('-MAINVIEW-')
    # ressources/logo.png ist im Repo nur ein generisches Platzhalter-Logo
    # (siehe README) - Ansible ueberschreibt es optional mit einem eigenen
    # Vereinslogo (ansible/files/logo.png, gitignored, siehe .gitignore).
    # Fehlt die Datei dennoch, bleibt das Blank-Screen-Wasserzeichen einfach
    # leer statt abzustuerzen.
    # Es gibt KEIN eigenes Sidebar-Logo - das Logo bleibt ausschliesslich dem
    # Blank-Screen vorbehalten ("Video aus", siehe blend_logo_centered()
    # unten), deshalb wird hier nur die unskalierte blank_logo-Variante
    # gebraucht.
    blank_logo = cv2.imread('ressources/logo.png', cv2.IMREAD_UNCHANGED)
    if blank_logo is None:
        print("ressources/logo.png nicht gefunden - Blank-Screen-Wasserzeichen bleibt leer.", file=sys.stderr)

    # --- Ersteinrichtungs-Assistent: Stand -> Ausschnitte -> PIN ---
    # Erzwungen (kein Abbrechen zum Hauptbildschirm) solange die jeweilige
    # Voraussetzung fehlt - jeder Schritt liest/schreibt ausschliesslich
    # Dateien auf /boot/firmware, nie nur In-Memory-Zustand: ein
    # Stromausfall/Neustart zu JEDEM Zeitpunkt fuehrt beim naechsten Start
    # einfach wieder zu genau dem Schritt, der noch fehlt.
    stands = load_stands()
    active_stand = load_active_stand(stands, cfg)
    while active_stand is None:
        stands = load_stands()
        chosen = run_stand_select(stands, forced=True)
        if chosen is not None and save_active_stand(chosen['id']):
            active_stand = chosen

    StreamPath = active_stand['url']
    set_stand_name(active_stand['displayName'])

    # Kamera bewusst noch OHNE crop_region konstruiert - waehrend der
    # Ausschnitts-Kalibrierung unten wird ohnehin nur cap.getFrame(full=True)
    # gebraucht (siehe edit_section_points), das ist von crop_region
    # unabhaengig. crop_region wird weiter unten, sobald bekannt, direkt am
    # laufenden Camera-Objekt gesetzt (kein Neuaufbau/Reconnect noetig -
    # der Hintergrund-Thread liest das Attribut bei jedem Frame neu ein).
    cap = Camera(StreamPath)

    section_full_orig, section_detail_orig = load_sections(cfg)
    while section_full_orig is None or section_detail_orig is None:
        frame_or_back = wait_for_camera_frame(cap)
        # isinstance-Check statt direktem "== 'back'": frame_or_back ist im
        # Erfolgsfall ein numpy-Array (der Kamera-Frame) - ein Array mit
        # einem String zu vergleichen wirft "ValueError: The truth value of
        # an array... is ambiguous" statt einfach False zu liefern (live am
        # Test-Pi als echter, durch SuccessExitStatus=1 verdeckter Absturz
        # aufgefallen).
        if isinstance(frame_or_back, str) and frame_or_back == 'back':
            # Zurueck zur Stand-Auswahl, z.B. weil die URL falsch war -
            # danach muss diese Kamera-Verbindung durch eine neue ersetzt
            # werden, sobald ein (ggf. anderer) Stand feststeht.
            active_stand = None
            while active_stand is None:
                stands = load_stands()
                chosen = run_stand_select(stands, forced=True)
                if chosen is not None and save_active_stand(chosen['id']):
                    active_stand = chosen
            StreamPath = active_stand['url']
            set_stand_name(active_stand['displayName'])
            # cap.stop() VOR dem Ersetzen: ohne das lief der Hintergrund-
            # Thread des verworfenen (ggf. nicht erreichbaren) alten Standes
            # als Daemon unbegrenzt weiter und versuchte alle 2s erfolglos,
            # dessen URL erneut zu verbinden - gefunden beim Fresh-Install-
            # Test, Fix uebernommen aus main (camera.py, Commit 0bbd28e).
            cap.stop()
            cap = Camera(StreamPath)
            continue
        which = 'full' if section_full_orig is None else 'detail'
        label = 'Ganze Scheibe' if which == 'full' else 'Innen Scheibe'
        # other_points: falls der jeweils ANDERE Bereich schon feststeht
        # (z.B. "Innen Scheibe" nach bereits gespeicherter "Ganze Scheibe"),
        # wird er als graue Referenz mitgezeichnet - gleiches Verhalten wie
        # im freiwilligen Settings-Menue.
        other = section_detail_orig if which == 'full' else section_full_orig
        result = edit_section_points(label, cap, None, other_points=other, allow_cancel=False, will_restart=False)
        if result is not None:
            new_full = result if which == 'full' else section_full_orig
            new_detail = result if which == 'detail' else section_detail_orig
            save_sections_override(new_full, new_detail)
            section_full_orig, section_detail_orig = load_sections(cfg)

    SettingsPin = load_settings_pin(cfg)
    while SettingsPin == DEFAULT_PIN:
        new_pin = change_pin_flow(SettingsPin, forced=True)
        if new_pin is not None:
            SettingsPin = new_pin

    pts_full = np.array(section_full_orig, dtype="int")
    pts_detail = np.array(section_detail_orig, dtype="int")

    # nur den fuer section_full/section_detail benoetigten Bildausschnitt
    # aus der Kamera holen statt des vollen Frames - spart Kopier-/
    # Verarbeitungskosten, das Warp-Ergebnis bleibt dabei unveraendert
    CROP_MARGIN = 15
    crop_x0, crop_y0, crop_x1, crop_y1 = tl.crop_bounds([pts_full, pts_detail], CROP_MARGIN)
    pts_full = pts_full - [crop_x0, crop_y0]
    pts_detail = pts_detail - [crop_x0, crop_y0]
    cap.crop_region = (crop_x0, crop_y0, crop_x1, crop_y1)

    # Perspektiv-Matrizen einmalig berechnen statt bei jedem Frame neu -
    # pts_full/pts_detail aendern sich zur Laufzeit nie (nur ein Neustart
    # nach einer Settings-Aenderung setzt sie neu). dsize=VideoSize direkt
    # hier hineingerechnet spart ausserdem das bisher separate cv2.resize()
    # auf VideoSize nach dem Warp - cv2.warpPerspective() liefert das Bild
    # im Hot-Loop unten in einem Rutsch schon in der richtigen Groesse.
    M_full = tl.compute_perspective_matrix(pts_full, VideoSize)
    M_detail = tl.compute_perspective_matrix(pts_detail, VideoSize)

    show_page('-MAINVIEW-')

    while True:
        # Nur pruefen, solange das Kamerabild angezeigt wird: Timer-Serien und
        # der Blank-Screen brauchen die Kamera nicht, ein Stream-Ausfall darf
        # sie nicht durch einen Prozess-Neustart unterbrechen. camera.py
        # verbindet sich im Hintergrund selbststaendig neu.
        if (stale_check_due(displayVideo, displayTimer, time.monotonic(), video_resumed_at, STREAM_STALE_TIMEOUT_SEC)
                and cap.is_stale(STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC)):
            if cap.frame_id == 0:
                print(f"Kein Kamera-Frame innerhalb der Startup-Frist von {STREAM_STARTUP_TIMEOUT_SEC}s erhalten "
                      f"(seit Prozessstart: {time.monotonic() - cap.start_time:.1f}s) - beende Prozess fuer Neustart.", file=sys.stderr)
            else:
                print(f"Kein neuer Kamera-Frame seit {time.monotonic() - cap.last_frame_time:.1f}s "
                      f"(Schwelle {STREAM_STALE_TIMEOUT_SEC}s, zuletzt frame_id={cap.frame_id}) - beende Prozess fuer Neustart.", file=sys.stderr)
            # Exit-Code 2, NICHT 1: xinit gibt bei einem direkt an sich selbst
            # gerichteten SIGTERM (z.B. "systemctl stop/restart") selbst
            # Exit-Code 1 zurueck ("unexpected signal", siehe
            # targetdisplay.service.j2::SuccessExitStatus) - ein echter
            # Stream-Ausfall braucht einen eigenen, davon unterscheidbaren
            # Code (Python-Abstuerze enden mit 3, siehe _handle_uncaught).
            sys.exit(2)

        event, values = window.read(timeout=MAIN_LOOP_TICK_MS)
        ### Button handling
        if event == WIN_CLOSED:
            break
        elif event == '-TOGGLEVIDEO-':
            displayVideo = not displayVideo
            if displayVideo:
              video_resumed_at = time.monotonic()
              window['-TOGGLEVIDEO-'].widget.config(image=window._icon('eye_slash_neutral'))
              # frame_count/frame_timestamps bewusst NICHT zurueckgesetzt - Video
              # aus/ein soll die FPS/Frame-Anzeige nur pausieren (sie friert waehrend
              # des Blank-Screens einfach ein, da weder der displayVideo- noch der
              # displayTimer-Zweig unten dann laeuft) und beim Wiedereinschalten
              # nahtlos weiterzaehlen, genau wie beim Timer - kein Reset auf 1/leer.
              zoom_disabled(False)
              sync_reset_button(zoom_level, zoom_center)
              blink_disabled(False)
              timer_disabled(False)
            else:
              window['-TOGGLEVIDEO-'].widget.config(image=window._icon('eye_neutral'))
              frame = np.zeros((VideoSize[1], VideoSize[0], 3), np.uint8)
              if blank_logo is not None:
                frame = blend_logo_centered(frame, blank_logo)
              window.draw_image(frame)
              # Ausgeblendet, nicht zurueckgesetzt (frame_count/frame_timestamps
              # bleiben unangetastet, siehe oben) - analog zum Timer, der die
              # Anzeige waehrend seiner eigenen Blank-Phase ebenso leert.
              window_fps.update('')
              zoom_disabled(True)
              sync_reset_button(zoom_level, zoom_center, globally_disabled=True)
              blink_disabled(True)
              timer_disabled(True)
        elif event == '-SETTINGS-':
            if check_pin(SettingsPin):
                if run_settings_flow(cap, section_full_orig, section_detail_orig, stands, SettingsPin):
                    # Neue Werte sind persistiert (Boot-Partition-Override) -
                    # sauberster Weg fuer einen neu berechneten Crop-Bereich
                    # ist ein Neustart des Prozesses; Restart=always im
                    # systemd-Unit startet main.py sofort mit den neuen
                    # Werten neu (Exit-Code 0 = regulaeres Beenden).
                    sys.exit(0)
        elif event == '-FULL_VIDEO-':
          zoom_level = 'full'
          zoom_center = []
          sync_reset_button(zoom_level, zoom_center)
        elif event == '-DETAIL_VIDEO-':
          zoom_level = 'detail'
          zoom_center = []
          sync_reset_button(zoom_level, zoom_center)
        elif event == '-VIDEO-':
          if zoom_center == []:
            # init zoom
            zoom_center = values["-VIDEO-"]
          else:
            move_speed = 5
            # move zoomed window
            if values["-VIDEO-"][0] < 30:
              zoom_center = zoom_center[0]-move_speed,zoom_center[1]
            elif values["-VIDEO-"][0] > 70:
              zoom_center = zoom_center[0]+move_speed,zoom_center[1]
            if values["-VIDEO-"][1] < 30:
              zoom_center = zoom_center[0],zoom_center[1]-move_speed
            elif values["-VIDEO-"][1] > 70:
              zoom_center = zoom_center[0],zoom_center[1]+move_speed
          zoom_center = clamp_zoom_center(zoom_center)
          sync_reset_button(zoom_level, zoom_center)
        elif event == '-RESETZOOM-':
          # Setzt nicht nur den manuellen Pan zurueck, sondern auch den
          # Zoom-Modus auf "Ganze Scheibe" - sonst wuerde ein aktiver "Innen
          # Scheibe"-Zoom nach Reset bestehen bleiben.
          zoom_level = 'full'
          zoom_center = []
          sync_reset_button(zoom_level, zoom_center)
        elif event == '-BLINK_START-':
          # Zoom (Buttons, Klick-Pan UND Reset) bleibt waehrend Blinken
          # bewusst fuer alle drei Bedienwege einheitlich nutzbar: Crop wird
          # pro Frame nach der Referenz/Live-Auswahl angewendet, betrifft
          # also beide Zoom-Zustaende gleich, funktioniert also einwandfrei
          # waehrend des Blinkens.
          timer_disabled(True)
          video_filter_disabled(True)
          set_icon_buttons(('-BLINK_START-',), False)
          set_icon_buttons(('-BLINK_STOP-', '-BLINK_REF-'), True)
          blink_ref = []
          blink = True
        elif event == '-BLINK_REF-':
          blink_ref = []
          blink = True
        elif event == '-BLINK_STOP-':
          timer_disabled(False)
          blink_disabled(False)
          video_filter_disabled(False)
          blink_ref = []
          blink = False
        elif event in ('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'):
          # Alle drei Timer-Varianten starten identisch - nur timerType
          # (=event) unterscheidet, welcher Ablauf (timerlib) gerendert wird.
          zoom_disabled(True)
          sync_reset_button(zoom_level, zoom_center, globally_disabled=True)
          blink_disabled(True)
          video_filter_disabled(True)
          displayTimer = True
          set_icon_buttons(('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'), False)
          set_icon_buttons(('-TIMER_STOP-',), True)
          displayVideo = False
          timerType = event
          timerStart = time.monotonic()
          last_timer_state = None
        elif event == '-TIMER_STOP-':
          zoom_disabled(False)
          sync_reset_button(zoom_level, zoom_center)
          blink_disabled(False)
          video_filter_disabled(False)
          displayTimer = False
          displayVideo = True
          video_resumed_at = time.monotonic()
          set_icon_buttons(('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'), True)
          set_icon_buttons(('-TIMER_STOP-',), False)


        ### Image handling
        if displayVideo:
          # skip reprocessing/redrawing if the camera hasn't delivered a new frame yet
          current_frame_id = cap.frame_id
          frame = cap.getFrame() if current_frame_id != last_frame_id else None
          if frame is not None:
            last_frame_id = current_frame_id

            #ready to display, all image manipulations are done only display options from here
            #--------------------------------------------------------------------------
            #blink
            # blink_ref haelt bewusst das ROHE, noch NICHT per
            # warpPerspective() entzerrte Kamerabild fest, nicht das fertig
            # entzerrte - Ganze/Innen Scheibe sind zwei VERSCHIEDENE
            # Matrizen (M_full/M_detail) auf demselben Rohbild, keine
            # ineinander verschachtelten Transformationen. Ein bereits
            # entzerrtes Referenzbild wuerde nach einem Zoom-Stufen-Wechsel
            # auf der alten Perspektive haengen bleiben, waehrend neue
            # Live-Frames schon die neue zeigen - sichtbares Springen beim
            # Blinken. Das ROHE Bild laesst sich dagegen bei JEDEM
            # Zoom-Wechsel einfach mit der jeweils aktuellen Matrix neu
            # entzerren, waehrend der tatsaechlich fotografierte Inhalt (und
            # damit die Einschusslöcher zum Referenzzeitpunkt) unveraendert
            # erhalten bleibt. WICHTIG: blink_ref wird NIE automatisch neu
            # aufgenommen, nur bei explizitem -BLINK_START-/-BLINK_REF- -
            # eine automatische Neuaufnahme (z.B. bei jedem Zoom-Wechsel)
            # wuerde die eigentliche Referenz-Funktion (alte vs. neue
            # Treffer vergleichen) zerstoeren.
            if blink and len(blink_ref) == 0:
              blink_ref = frame
            M_current = M_full if not zoom_level == 'detail' else M_detail
            if blink and int(time.monotonic()) % 2 == 1:
              display_frame = cv2.warpPerspective(blink_ref, M_current, VideoSize)
            else:
              display_frame = cv2.warpPerspective(frame, M_current, VideoSize)

            # zoom
            if zoom_center != []: display_frame = tl.crop(display_frame, VIDEO_ZOOM_FACTOR, zoom_center)

            window.draw_image(display_frame)
            frame_count += 1
            now = time.monotonic()
            frame_timestamps.append(now)
            while frame_timestamps and (now - frame_timestamps[0]) > FPS_WINDOW_SECONDS:
              frame_timestamps.popleft()
            try:
              window_span = now - frame_timestamps[0]
              fps = len(frame_timestamps) / window_span
              window_fps.update(f'FPS: {str(round(fps,1)) } - Frame { str(frame_count) }')
            except ZeroDivisionError:
              pass
        elif displayTimer:
          # VideoSize ist (Breite, Hoehe), numpy-Arrays erwarten
          # (Hoehe, Breite, Kanaele) - deshalb hier bewusst vertauscht,
          # analog zum Blank-Screen-Handler oben (-TOGGLEVIDEO-).
          state = timerlib.timer_state(timerType, time.monotonic() - timerStart)
          # Nur neu zeichnen, wenn sich der Anzeigezustand aendert (hoechstens
          # einmal pro Sekunde) - das Bild wird sonst in jedem Schleifendurchlauf
          # identisch neu aufgebaut und an Tk uebergeben.
          if state != last_timer_state:
            last_timer_state = state
            frame = np.zeros((VideoSize[1],VideoSize[0],3), np.uint8)
            frame[:] = (0, 255, 0) if state.color == 'green' else (0, 0, 255)
            if state.number is not None:
              cv2.putText(frame, str(state.number), (20,130), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            if state.countdown is not None:
              draw_timer_countdown(frame, state.countdown)
            window.draw_image(frame)
            window_fps.update('')
          if state.finished:
            window.post('-TIMER_STOP-')

        now = datetime.now()
        # Nur bei tatsaechlicher Aenderung neu zeichnen (Datum/Uhrzeit
        # aendern sich hoechstens einmal pro Sekunde, dieser Loop-Tick
        # laeuft aber alle ~10ms) - spart bei rund 99% der Iterationen ein
        # unnoetiges Tk-Redraw dieser beiden Labels.
        date_str = now.strftime("%d.%m.%Y")
        if date_str != last_date_str:
            window_date.update(date_str)
            last_date_str = date_str
        time_str = now.strftime("%H:%M:%S")
        if time_str != last_time_str:
            window_time.update(time_str)
            last_time_str = time_str
    window.close()

if __name__ == '__main__':
    main()


