import sys
import time
from collections import deque
from datetime import datetime

import cv2
import numpy as np

import camera
import process_handlers
import replay
import timerlib
import transformlib as tl
import ui
import yamlconfig
from camera import STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC, Camera
from flows import (
    SECTION_LABELS,
    change_pin_flow,
    check_pin,
    edit_section_points,
    run_settings_flow,
    run_stand_select,
    wait_for_camera_frame,
    with_section,
)
from overlays import (
    blend_logo_centered,
    draw_replay_marker,
    draw_timer_countdown,
    replay_no_frame,
    replay_separator,
)
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
    blink_buttons,
    blink_disabled,
    replay_buttons,
    set_icon_buttons,
    set_stand_name,
    show_page,
    sync_reset_button,
    timer_disabled,
    video_filter_disabled,
    zoom_disabled,
)
from watchdog import stale_check_due

# Takt der Hauptschleife (Neuzeichnen, Timer, Uhr). Tasteneingaben wecken
# window.read() sofort, der Takt bestimmt nur, wie schnell ein neuer
# Kamera-Frame (ca. 6 pro Sekunde) oder ein Timer-Schritt auf dem Bildschirm
# erscheint.
MAIN_LOOP_TICK_MS = 33

version = '0.13.1'


def main():
    cfg = yamlconfig.load("config.yml")
    camera.log_version()
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
    # Aufnahme der Gruenphasen der letzten Timer-Serie (replay.py). Bleibt
    # nach "Replay Ende" erhalten und wird erst beim Start der naechsten
    # Serie verworfen.
    recording = None
    replay_type = None
    last_rec_frame_id = -1
    replay_active = False
    replay_start = time.monotonic()
    last_replay_key = None
    replay_latency, replay_budget = replay.read_settings(cfg)

    window = ui.window = ui.Window(cfg, VideoSize, version)

    # Einmal nachschlagen statt in jedem Schleifendurchlauf.
    window_date = window['-DATE-']
    window_time = window['-TIME-']
    window_fps = window['-FPS-']

    show_page('-MAINVIEW-')
    # Wasserzeichen fuer "Video aus". Im Repo liegt ein Platzhalter, Ansible
    # ersetzt ihn optional durch das Vereinslogo (ansible/files/logo.png).
    # Fehlt die Datei, bleibt der Bildschirm bei "Video aus" schwarz.
    blank_logo = cv2.imread('ressources/logo.png', cv2.IMREAD_UNCHANGED)
    if blank_logo is None:
        print("ressources/logo.png nicht gefunden - Blank-Screen-Wasserzeichen bleibt leer.", file=sys.stderr)

    # --- Ersteinrichtungs-Assistent: Stand -> Ausschnitte -> PIN ---
    # Erzwungen (kein Abbrechen zum Hauptbildschirm) solange die jeweilige
    # Voraussetzung fehlt - jeder Schritt liest/schreibt ausschliesslich
    # Dateien auf /boot/firmware, nie nur In-Memory-Zustand: ein
    # Stromausfall/Neustart zu JEDEM Zeitpunkt fuehrt beim naechsten Start
    # einfach wieder zu genau dem Schritt, der noch fehlt.
    def choose_stand():
        # Pflichtauswahl, bis ein Stand gespeichert ist.
        nonlocal stands
        while True:
            stands = load_stands()
            chosen = run_stand_select(stands, forced=True)
            if chosen is not None and save_active_stand(chosen['id']):
                return chosen

    stands = load_stands()
    active_stand = load_active_stand(stands, cfg)
    if active_stand is None:
        active_stand = choose_stand()

    StreamPath = active_stand['url']
    set_stand_name(active_stand['displayName'])

    # Zunaechst ohne Zuschnitt: die Kalibrierung braucht das volle Bild. Der
    # Zuschnitt wird unten am laufenden Objekt gesetzt, ohne neue Verbindung.
    cap = Camera(StreamPath)

    section_full_orig, section_detail_orig = load_sections(cfg)
    while section_full_orig is None or section_detail_orig is None:
        frame_or_back = wait_for_camera_frame(cap)
        # Im Erfolgsfall ist frame_or_back ein numpy-Array; ein direkter
        # Vergleich mit 'back' wuerde ValueError werfen.
        if isinstance(frame_or_back, str) and frame_or_back == 'back':
            # Zurueck zur Stand-Auswahl, z.B. weil die URL falsch war -
            # danach muss diese Kamera-Verbindung durch eine neue ersetzt
            # werden, sobald ein (ggf. anderer) Stand feststeht.
            active_stand = choose_stand()
            StreamPath = active_stand['url']
            set_stand_name(active_stand['displayName'])
            # Alte Verbindung beenden, sonst versucht ihr Lesethread endlos,
            # die verworfene URL zu erreichen.
            cap.stop()
            cap = Camera(StreamPath)
            continue
        which = 'full' if section_full_orig is None else 'detail'
        # other_points: falls der jeweils ANDERE Bereich schon feststeht
        # (z.B. "Innen Scheibe" nach bereits gespeicherter "Ganze Scheibe"),
        # wird er als graue Referenz mitgezeichnet - gleiches Verhalten wie
        # im freiwilligen Settings-Menue.
        other = section_detail_orig if which == 'full' else section_full_orig
        result = edit_section_points(SECTION_LABELS[which], cap, None, other_points=other,
                                     allow_cancel=False, will_restart=False)
        if result is not None:
            save_sections_override(*with_section(which, result, section_full_orig, section_detail_orig))
            section_full_orig, section_detail_orig = load_sections(cfg)

    SettingsPin = load_settings_pin(cfg)
    while SettingsPin == DEFAULT_PIN:
        new_pin = change_pin_flow(SettingsPin, forced=True)
        if new_pin is not None:
            SettingsPin = new_pin

    pts_full = np.array(section_full_orig, dtype="int")
    pts_detail = np.array(section_detail_orig, dtype="int")

    # Die Kamera liefert nur den Bereich, den die beiden Ausschnitte brauchen.
    # Das spart Kopier- und Rechenzeit, das entzerrte Bild bleibt gleich.
    CROP_MARGIN = 15
    crop_x0, crop_y0, crop_x1, crop_y1 = tl.crop_bounds([pts_full, pts_detail], CROP_MARGIN)
    pts_full = pts_full - [crop_x0, crop_y0]
    pts_detail = pts_detail - [crop_x0, crop_y0]
    cap.set_crop_region((crop_x0, crop_y0, crop_x1, crop_y1))

    # Perspektiv-Matrizen einmal berechnen: die Ausschnitte aendern sich nur
    # mit einem Neustart. Sie bilden direkt auf VideoSize ab, ein eigenes
    # Skalieren entfaellt.
    M_full = tl.compute_perspective_matrix(pts_full, VideoSize)
    M_detail = tl.compute_perspective_matrix(pts_detail, VideoSize)

    def render(raw):
        # Rohbild -> Anzeigebild: Entzerrung der aktuellen Zoomstufe, dann der
        # manuelle Zoom. Gemeinsam fuer Livebild, Blinken und Replay.
        M_current = M_full if not zoom_level == 'detail' else M_detail
        out = cv2.warpPerspective(raw, M_current, VideoSize)
        if zoom_center != []:
            out = tl.crop(out, VIDEO_ZOOM_FACTOR, zoom_center)
        return out

    show_page('-MAINVIEW-')

    while True:
        # Nur pruefen, solange das Kamerabild angezeigt wird: Timer-Serien und
        # der Blank-Screen brauchen die Kamera nicht, ein Stream-Ausfall darf
        # sie nicht durch einen Prozess-Neustart unterbrechen. camera.py
        # verbindet sich im Hintergrund selbststaendig neu.
        # Waehrend des Replays ebenso wenig: es zeigt die Aufnahme, nicht den
        # Stream.
        if (stale_check_due(displayVideo and not replay_active, displayTimer, time.monotonic(), video_resumed_at, STREAM_STALE_TIMEOUT_SEC)
                and cap.is_stale(STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC)):
            # snapshot() liefert frame_id/last_frame_time/start_time aus
            # einem einzigen Lock-Zugriff, statt sie einzeln zu lesen - sonst
            # koennten die beiden Werte in der Meldung unten aus zwei
            # verschiedenen Momenten stammen.
            frame_id, last_frame_time, start_time = cap.snapshot()
            if frame_id == 0:
                print(f"Kein Kamera-Frame innerhalb der Startup-Frist von {STREAM_STARTUP_TIMEOUT_SEC}s erhalten "
                      f"(seit Prozessstart: {time.monotonic() - start_time:.1f}s) - beende Prozess fuer Neustart.", file=sys.stderr)
            else:
                print(f"Kein neuer Kamera-Frame seit {time.monotonic() - last_frame_time:.1f}s "
                      f"(Schwelle {STREAM_STALE_TIMEOUT_SEC}s, zuletzt frame_id={frame_id}) - beende Prozess fuer Neustart.", file=sys.stderr)
            # Eigener Exit-Code 2 (Stream-Ausfall), im Journal als
            # "TargetDisplay beendet (exit 2)" von play_it sichtbar.
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
                # frame_count/frame_timestamps laufen weiter, die FPS-Anzeige
                # pausiert nur (wie beim Timer).
                zoom_disabled(False)
                sync_reset_button(zoom_level, zoom_center)
                blink_disabled(False)
                timer_disabled(False)
                replay_buttons(recording is not None and recording.has_frames(), False)
            else:
                window['-TOGGLEVIDEO-'].widget.config(image=window._icon('eye_neutral'))
                frame = np.zeros((VideoSize[1], VideoSize[0], 3), np.uint8)
                if blank_logo is not None:
                    frame = blend_logo_centered(frame, blank_logo)
                window.draw_image(frame)
                # FPS-Anzeige nur leeren, die Zaehler bleiben (siehe oben).
                window_fps.update('')
                zoom_disabled(True)
                sync_reset_button(zoom_level, zoom_center, globally_disabled=True)
                blink_disabled(True)
                timer_disabled(True)
                replay_buttons(False, False)
        elif event == '-SETTINGS-':
            if check_pin(SettingsPin):
                if run_settings_flow(cap, section_full_orig, section_detail_orig, stands, SettingsPin):
                    # Gespeichert: Prozess beenden (Exit 0), systemd startet
                    # ihn mit den neuen Werten neu.
                    sys.exit(0)
        elif event in ('-FULL_VIDEO-', '-RESETZOOM-'):
            # Reset setzt Zoomstufe und manuellen Ausschnitt zurueck, wirkt
            # also wie "Ganze Scheibe".
            zoom_level = 'full'
            zoom_center = []
            sync_reset_button(zoom_level, zoom_center)
        elif event == '-DETAIL_VIDEO-':
            zoom_level = 'detail'
            zoom_center = []
            sync_reset_button(zoom_level, zoom_center)
        elif event == '-VIDEO-':
            if zoom_center == []:
                # Erster Tipp: Mittelpunkt des Ausschnitts.
                zoom_center = values["-VIDEO-"]
            else:
                # Tipp nahe am Rand: Ausschnitt in diese Richtung verschieben.
                move_speed = 5
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
        elif event == '-BLINK_START-':
            # Zoom bleibt bedienbar, er wirkt auf Referenz und Livebild gleich.
            # Die Timer bleiben startbar (siehe Timer-Start unten).
            video_filter_disabled(True)
            blink_buttons(True)
            blink_ref = []
            blink = True
        elif event == '-BLINK_REF-':
            blink_ref = []
            blink = True
        elif event == '-BLINK_STOP-':
            blink_disabled(False)
            video_filter_disabled(False)
            blink_ref = []
            blink = False
        elif event in timerlib.TIMER_KEYS:
            # Alle drei Timer-Varianten starten identisch - nur timerType
            # (=event) unterscheidet, welcher Ablauf (timerlib) gerendert wird.
            # Ein laufendes Blinken pausiert nur: blink und blink_ref bleiben
            # unveraendert, nach dem Timer blinkt das Bild mit der Referenz von
            # vor der Serie weiter und zeigt so die Treffer der Serie.
            zoom_disabled(True)
            sync_reset_button(zoom_level, zoom_center, globally_disabled=True)
            blink_disabled(True)
            video_filter_disabled(True)
            displayTimer = True
            set_icon_buttons(timerlib.TIMER_KEYS, False)
            set_icon_buttons(('-TIMER_STOP-',), True)
            displayVideo = False
            timerType = event
            timerStart = time.monotonic()
            last_timer_state = None
            # Die Aufnahme der vorigen Serie zuerst freigeben, dann die neue
            # anlegen - sonst lagen kurzzeitig beide im RAM.
            recording = None
            recording = replay.Recording(timerlib.green_windows(timerType), replay_latency, replay_budget)
            replay_type = timerType
            last_rec_frame_id, _, _ = cap.snapshot()
            replay_buttons(False, False)
        elif event == '-TIMER_STOP-':
            zoom_disabled(False)
            sync_reset_button(zoom_level, zoom_center)
            blink_buttons(blink)
            # Video aus bleibt gesperrt, solange Blinken weiterlaeuft - wie
            # nach -BLINK_START-.
            video_filter_disabled(blink)
            displayTimer = False
            displayVideo = True
            video_resumed_at = time.monotonic()
            set_icon_buttons(timerlib.TIMER_KEYS, True)
            set_icon_buttons(('-TIMER_STOP-',), False)
            if recording is not None:
                print(f"Replay-Aufnahme: {recording.frame_count()} Bilder, "
                      f"{recording.bytes / 1024 / 1024:.0f} MB, jedes {recording.step}. Bild", file=sys.stderr)
            replay_buttons(recording is not None and recording.has_frames(), False)
        elif event == '-REPLAY-':
            # Startet das Replay von vorn. Waehrend des Replays sendet derselbe
            # Button "-REPLAY_END-" (siehe ui.replay_buttons()). Zoom bleibt
            # bedienbar, Blinken pausiert (Zustand und Referenz bleiben), Timer
            # und Video aus sind gesperrt. Bei "Video aus" und waehrend eines
            # Timers ist der Button gesperrt.
            if recording is not None and recording.has_frames() and displayVideo and not displayTimer:
                replay_active = True
                replay_start = time.monotonic()
                last_replay_key = None
                blink_disabled(True)
                timer_disabled(True)
                video_filter_disabled(True)
                replay_buttons(True, True)
        elif event == '-REPLAY_END-':
            # Zurueck zum Livebild. Die Aufnahme bleibt fuer ein weiteres Replay
            # erhalten.
            replay_active = False
            blink_buttons(blink)
            timer_disabled(False)
            video_filter_disabled(blink)
            replay_buttons(True, False)
            video_resumed_at = time.monotonic()
            last_frame_id = -1


        ### Image handling
        view = recording.view(time.monotonic() - replay_start) if replay_active else None
        if replay_active and view is None:
            # Nach dem roten Schlussbild endet das Replay von selbst, wie mit
            # "Replay Ende". replay_active sofort zuruecksetzen, damit das
            # Ereignis nur einmal gesendet wird.
            replay_active = False
            window.post('-REPLAY_END-')
        elif replay_active:
            segments = len(recording.windows)
            counter = replay.counter_text(view, segments)
            # Neu zeichnen nur, wenn sich Bild, Zaehler (Zehntelsekunden) oder
            # Zoom aendern.
            replay_key = (view.key, counter, zoom_level, tuple(zoom_center))
            if replay_key != last_replay_key:
                last_replay_key = replay_key
                if view.kind == 'start':
                    display_frame = replay_separator(VideoSize, timerlib.TITLES[replay_type])
                elif view.kind == 'separator':
                    display_frame = replay_separator(VideoSize, f'Durchgang {view.segment + 1}/{segments}')
                elif view.kind == 'end':
                    display_frame = replay_separator(VideoSize, '')
                elif view.frame is None:
                    display_frame = replay_no_frame(VideoSize)
                else:
                    display_frame = render(view.frame)
                window.draw_image(draw_replay_marker(display_frame, counter))
                window_fps.update('')
        elif displayVideo:
            # Nur neu zeichnen, wenn die Kamera ein neues Bild geliefert hat.
            current_frame_id, _, _ = cap.snapshot()
            frame = cap.getFrame() if current_frame_id != last_frame_id else None
            if frame is not None:
                last_frame_id = current_frame_id

                # Blinken: blink_ref ist das rohe, noch nicht entzerrte Bild.
                # So passt die Referenz nach einem Wechsel der Zoomstufe sofort
                # zur neuen Perspektive. Neu aufgenommen wird sie nur mit Start
                # oder Referenz, nie automatisch - sonst ginge der Vergleich
                # alter und neuer Treffer verloren.
                if blink and len(blink_ref) == 0:
                    blink_ref = frame
                if blink and int(time.monotonic()) % 2 == 1:
                    display_frame = render(blink_ref)
                else:
                    display_frame = render(frame)

                window.draw_image(display_frame)
                frame_count += 1
                now = time.monotonic()
                frame_timestamps.append(now)
                while frame_timestamps and (now - frame_timestamps[0]) > FPS_WINDOW_SECONDS:
                    frame_timestamps.popleft()
                window_span = now - frame_timestamps[0]
                if window_span > 0:
                    fps = len(frame_timestamps) / window_span
                    window_fps.update(f'FPS: {str(round(fps,1)) } - Frame { str(frame_count) }')
        elif displayTimer:
            timer_elapsed = time.monotonic() - timerStart
            # Aufnahme: jedes neue Kamerabild geht an die Aufnahme, die nur
            # behaelt, was in eine Gruenphase faellt.
            if not recording.is_complete(timer_elapsed):
                rec_frame_id, _, _ = cap.snapshot()
                if rec_frame_id != last_rec_frame_id:
                    last_rec_frame_id = rec_frame_id
                    rec_frame = cap.getFrame()
                    if rec_frame is not None:
                        recording.add(rec_frame, timer_elapsed)
            state = timerlib.timer_state(timerType, timer_elapsed)
            # Nur neu zeichnen, wenn sich der Anzeigezustand aendert (hoechstens
            # einmal pro Sekunde) - das Bild wird sonst in jedem Schleifendurchlauf
            # identisch neu aufgebaut und an Tk uebergeben.
            if state != last_timer_state:
                last_timer_state = state
                # numpy erwartet (Hoehe, Breite), VideoSize ist (Breite, Hoehe).
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
        # Datum und Uhrzeit nur neu zeichnen, wenn sich der Text aendert
        # (hoechstens einmal pro Sekunde statt bei jedem Schleifendurchlauf).
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
    process_handlers.install()
    main()


