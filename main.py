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
    blank_frame,
    draw_replay_marker,
    replay_no_frame,
    replay_separator,
    timer_frame,
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

# Zeitraum fuer die FPS-Anzeige.
FPS_WINDOW_SECONDS = 60

# Rand um die beiden Ausschnitte beim Zuschnitt des Kamerabilds (Pixel).
CROP_MARGIN = 15

# Schrittweite (Prozent) und Randzonen (Prozent der Bildbreite/-hoehe) beim
# Verschieben des manuellen Ausschnitts per Tippen.
PAN_STEP = 5
PAN_EDGE_LOW, PAN_EDGE_HIGH = 30, 70

version = '0.13.1'


def run_setup_wizard(cfg):
    """Ersteinrichtungs-Assistent: Stand, Ausschnitte, PIN.

    Jeder Schritt erscheint nur, solange seine Einstellung fehlt, und laesst
    sich nicht abbrechen. Gespeichert wird jeder Schritt sofort auf
    /boot/firmware: nach einem Stromausfall geht es beim naechsten Start
    genau mit dem fehlenden Schritt weiter.

    Liefert (stands, cap, section_full, section_detail, settings_pin).
    """
    stands = load_stands()

    def choose_stand():
        # Pflichtauswahl, bis ein Stand gespeichert ist.
        nonlocal stands
        while True:
            stands = load_stands()
            chosen = run_stand_select(stands, forced=True)
            if chosen is not None and save_active_stand(chosen['id']):
                return chosen

    active_stand = load_active_stand(stands, cfg)
    if active_stand is None:
        active_stand = choose_stand()
    set_stand_name(active_stand['displayName'])

    # Zunaechst ohne Zuschnitt: die Kalibrierung braucht das volle Bild. Der
    # Zuschnitt wird spaeter am laufenden Objekt gesetzt, ohne neue Verbindung.
    cap = Camera(active_stand['url'])

    section_full, section_detail = load_sections(cfg)
    while section_full is None or section_detail is None:
        frame_or_back = wait_for_camera_frame(cap)
        # Im Erfolgsfall ist frame_or_back ein numpy-Array; ein direkter
        # Vergleich mit 'back' wuerde ValueError werfen.
        if isinstance(frame_or_back, str) and frame_or_back == 'back':
            # Zurueck zur Stand-Auswahl, z.B. weil die URL falsch war.
            active_stand = choose_stand()
            set_stand_name(active_stand['displayName'])
            # Alte Verbindung beenden, sonst versucht ihr Lesethread endlos,
            # die verworfene URL zu erreichen.
            cap.stop()
            cap = Camera(active_stand['url'])
            continue
        which = 'full' if section_full is None else 'detail'
        # Der andere Ausschnitt, falls schon gespeichert, wird zur
        # Orientierung mitgezeichnet (wie im Einstellungen-Menue).
        other = section_detail if which == 'full' else section_full
        result = edit_section_points(SECTION_LABELS[which], cap, None, other_points=other,
                                     allow_cancel=False, will_restart=False)
        if result is not None:
            save_sections_override(*with_section(which, result, section_full, section_detail))
            section_full, section_detail = load_sections(cfg)

    settings_pin = load_settings_pin(cfg)
    while settings_pin == DEFAULT_PIN:
        new_pin = change_pin_flow(settings_pin, forced=True)
        if new_pin is not None:
            settings_pin = new_pin
    return stands, cap, section_full, section_detail, settings_pin


class MainScreen:
    """Zustand und Ablauf des Hauptbildschirms: Bedienereignisse
    (handle), Bildanzeige je Modus (draw), Stream-Ueberwachung
    (check_stream) und Uhr (update_clock)."""

    def __init__(self, window, video_size, cap, section_full, section_detail, stands,
                 settings_pin, replay_settings, blank_logo):
        self.window = window
        self.video_size = video_size
        self.cap = cap
        self.section_full = section_full
        self.section_detail = section_detail
        self.stands = stands
        self.settings_pin = settings_pin
        self.replay_latency, self.replay_budget = replay_settings
        self.blank_logo = blank_logo

        self.display_video = True
        self.display_timer = False
        self.video_resumed_at = time.monotonic()
        self.zoom_level = 'full'
        self.zoom_center = []
        self.blink = False
        self.blink_ref = []
        self.last_frame_id = -1
        self.frame_count = 1
        self.frame_timestamps = deque()
        self.timer_type = None
        self.timer_start = time.monotonic()
        self.last_timer_state = None
        # Aufnahme der Gruenphasen der letzten Timer-Serie (replay.py). Bleibt
        # nach "Replay Ende" erhalten und wird erst beim Start der naechsten
        # Serie verworfen.
        self.recording = None
        self.replay_type = None
        self.last_rec_frame_id = -1
        self.replay_active = False
        self.replay_start = time.monotonic()
        self.last_replay_key = None
        self.last_date_str = None
        self.last_time_str = None

        # Einmal nachschlagen statt in jedem Schleifendurchlauf.
        self.window_date = window['-DATE-']
        self.window_time = window['-TIME-']
        self.window_fps = window['-FPS-']

        pts_full = np.array(section_full, dtype="int")
        pts_detail = np.array(section_detail, dtype="int")
        # Die Kamera liefert nur den Bereich, den die beiden Ausschnitte
        # brauchen. Das spart Kopier- und Rechenzeit, das entzerrte Bild
        # bleibt gleich.
        x0, y0, x1, y1 = tl.crop_bounds([pts_full, pts_detail], CROP_MARGIN)
        cap.set_crop_region((x0, y0, x1, y1))
        # Perspektiv-Matrizen einmal berechnen: die Ausschnitte aendern sich
        # nur mit einem Neustart. Sie bilden direkt auf video_size ab, ein
        # eigenes Skalieren entfaellt.
        self.matrix_full = tl.compute_perspective_matrix(pts_full - [x0, y0], video_size)
        self.matrix_detail = tl.compute_perspective_matrix(pts_detail - [x0, y0], video_size)

        self._handlers = {
            '-TOGGLEVIDEO-': self.toggle_video,
            '-SETTINGS-': self.open_settings,
            '-FULL_VIDEO-': self.zoom_full,
            # Reset setzt Zoomstufe und manuellen Ausschnitt zurueck, wirkt
            # also wie "Ganze Scheibe".
            '-RESETZOOM-': self.zoom_full,
            '-DETAIL_VIDEO-': self.zoom_detail,
            '-VIDEO-': self.tap_video,
            '-BLINK_START-': self.blink_start,
            '-BLINK_REF-': self.blink_reference,
            '-BLINK_STOP-': self.blink_stop,
            '-TIMER_STOP-': self.timer_stop,
            '-REPLAY-': self.replay_start_clicked,
            '-REPLAY_END-': self.replay_end,
        }
        for key in timerlib.TIMER_KEYS:
            self._handlers[key] = self.timer_start_clicked

    # -- Hilfen ------------------------------------------------------------

    def render(self, raw):
        # Rohbild -> Anzeigebild: Entzerrung der aktuellen Zoomstufe, dann der
        # manuelle Zoom. Gemeinsam fuer Livebild, Blinken und Replay.
        matrix = self.matrix_detail if self.zoom_level == 'detail' else self.matrix_full
        out = cv2.warpPerspective(raw, matrix, self.video_size)
        if self.zoom_center != []:
            out = tl.crop(out, VIDEO_ZOOM_FACTOR, self.zoom_center)
        return out

    def has_recording(self):
        return self.recording is not None and self.recording.has_frames()

    # -- Stream-Ueberwachung -----------------------------------------------

    def check_stream(self):
        # Nur pruefen, solange das Kamerabild angezeigt wird: Timer-Serien,
        # der Blank-Screen und das Replay brauchen die Kamera nicht, ein
        # Stream-Ausfall darf sie nicht durch einen Prozess-Neustart
        # unterbrechen. camera.py verbindet sich im Hintergrund selbst neu.
        if not (stale_check_due(self.display_video and not self.replay_active, self.display_timer,
                                time.monotonic(), self.video_resumed_at, STREAM_STALE_TIMEOUT_SEC)
                and self.cap.is_stale(STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC)):
            return
        # snapshot() liefert die Werte aus einem einzigen Lock-Zugriff, sie
        # stammen also aus demselben Moment.
        frame_id, last_frame_time, start_time = self.cap.snapshot()
        if frame_id == 0:
            print(f"Kein Kamera-Frame innerhalb der Startup-Frist von {STREAM_STARTUP_TIMEOUT_SEC}s erhalten "
                  f"(seit Prozessstart: {time.monotonic() - start_time:.1f}s) - beende Prozess fuer Neustart.", file=sys.stderr)
        else:
            print(f"Kein neuer Kamera-Frame seit {time.monotonic() - last_frame_time:.1f}s "
                  f"(Schwelle {STREAM_STALE_TIMEOUT_SEC}s, zuletzt frame_id={frame_id}) - beende Prozess fuer Neustart.", file=sys.stderr)
        # Eigener Exit-Code 2 (Stream-Ausfall), im Journal als
        # "TargetDisplay beendet (exit 2)" von play_it sichtbar.
        sys.exit(2)

    # -- Bedienereignisse --------------------------------------------------

    def handle(self, event, values):
        handler = self._handlers.get(event)
        if handler is not None:
            handler(event, values)

    def toggle_video(self, event, values):
        self.display_video = not self.display_video
        if self.display_video:
            self.video_resumed_at = time.monotonic()
            self.window['-TOGGLEVIDEO-'].widget.config(image=self.window._icon('eye_slash_neutral'))
            # frame_count/frame_timestamps laufen weiter, die FPS-Anzeige
            # pausiert nur (wie beim Timer).
            zoom_disabled(False)
            sync_reset_button(self.zoom_level, self.zoom_center)
            blink_disabled(False)
            timer_disabled(False)
            replay_buttons(self.has_recording(), False)
        else:
            self.window['-TOGGLEVIDEO-'].widget.config(image=self.window._icon('eye_neutral'))
            self.window.draw_image(blank_frame(self.video_size, self.blank_logo))
            # FPS-Anzeige nur leeren, die Zaehler bleiben (siehe oben).
            self.window_fps.update('')
            zoom_disabled(True)
            sync_reset_button(self.zoom_level, self.zoom_center, globally_disabled=True)
            blink_disabled(True)
            timer_disabled(True)
            replay_buttons(False, False)

    def open_settings(self, event, values):
        if check_pin(self.settings_pin):
            if run_settings_flow(self.cap, self.section_full, self.section_detail, self.stands, self.settings_pin):
                # Gespeichert: Prozess beenden (Exit 0), systemd startet
                # ihn mit den neuen Werten neu.
                sys.exit(0)

    def zoom_full(self, event, values):
        self.zoom_level = 'full'
        self.zoom_center = []
        sync_reset_button(self.zoom_level, self.zoom_center)

    def zoom_detail(self, event, values):
        self.zoom_level = 'detail'
        self.zoom_center = []
        sync_reset_button(self.zoom_level, self.zoom_center)

    def tap_video(self, event, values):
        x, y = values['-VIDEO-']
        if self.zoom_center == []:
            # Erster Tipp: Mittelpunkt des Ausschnitts.
            center = (x, y)
        else:
            # Tipp nahe am Rand: Ausschnitt in diese Richtung verschieben.
            cx, cy = self.zoom_center
            if x < PAN_EDGE_LOW:
                cx -= PAN_STEP
            elif x > PAN_EDGE_HIGH:
                cx += PAN_STEP
            if y < PAN_EDGE_LOW:
                cy -= PAN_STEP
            elif y > PAN_EDGE_HIGH:
                cy += PAN_STEP
            center = (cx, cy)
        self.zoom_center = clamp_zoom_center(center)
        sync_reset_button(self.zoom_level, self.zoom_center)

    def blink_start(self, event, values):
        # Zoom bleibt bedienbar, er wirkt auf Referenz und Livebild gleich.
        # Die Timer bleiben startbar (siehe timer_start_clicked).
        video_filter_disabled(True)
        blink_buttons(True)
        self.blink_ref = []
        self.blink = True

    def blink_reference(self, event, values):
        self.blink_ref = []
        self.blink = True

    def blink_stop(self, event, values):
        blink_disabled(False)
        video_filter_disabled(False)
        self.blink_ref = []
        self.blink = False

    def timer_start_clicked(self, event, values):
        # Alle drei Timer-Varianten starten identisch - nur timer_type
        # (=event) unterscheidet, welcher Ablauf (timerlib) gerendert wird.
        # Ein laufendes Blinken pausiert nur: blink und blink_ref bleiben
        # unveraendert, nach dem Timer blinkt das Bild mit der Referenz von
        # vor der Serie weiter und zeigt so die Treffer der Serie.
        zoom_disabled(True)
        sync_reset_button(self.zoom_level, self.zoom_center, globally_disabled=True)
        blink_disabled(True)
        video_filter_disabled(True)
        self.display_timer = True
        set_icon_buttons(timerlib.TIMER_KEYS, False)
        set_icon_buttons(('-TIMER_STOP-',), True)
        self.display_video = False
        self.timer_type = event
        self.timer_start = time.monotonic()
        self.last_timer_state = None
        # Die Aufnahme der vorigen Serie zuerst freigeben, dann die neue
        # anlegen - sonst lagen kurzzeitig beide im RAM.
        self.recording = None
        self.recording = replay.Recording(timerlib.green_windows(event), self.replay_latency, self.replay_budget)
        self.replay_type = event
        self.last_rec_frame_id, _, _ = self.cap.snapshot()
        replay_buttons(False, False)

    def timer_stop(self, event, values):
        zoom_disabled(False)
        sync_reset_button(self.zoom_level, self.zoom_center)
        blink_buttons(self.blink)
        # Video aus bleibt gesperrt, solange Blinken weiterlaeuft - wie
        # nach -BLINK_START-.
        video_filter_disabled(self.blink)
        self.display_timer = False
        self.display_video = True
        self.video_resumed_at = time.monotonic()
        set_icon_buttons(timerlib.TIMER_KEYS, True)
        set_icon_buttons(('-TIMER_STOP-',), False)
        if self.recording is not None:
            print(f"Replay-Aufnahme: {self.recording.frame_count()} Bilder, "
                  f"{self.recording.bytes / 1024 / 1024:.0f} MB, jedes {self.recording.step}. Bild", file=sys.stderr)
        replay_buttons(self.has_recording(), False)

    def replay_start_clicked(self, event, values):
        # Startet das Replay von vorn. Waehrend des Replays sendet derselbe
        # Button "-REPLAY_END-" (siehe ui.replay_buttons()). Zoom bleibt
        # bedienbar, Blinken pausiert (Zustand und Referenz bleiben), Timer
        # und Video aus sind gesperrt. Bei "Video aus" und waehrend eines
        # Timers ist der Button gesperrt.
        if self.has_recording() and self.display_video and not self.display_timer:
            self.replay_active = True
            self.replay_start = time.monotonic()
            self.last_replay_key = None
            blink_disabled(True)
            timer_disabled(True)
            video_filter_disabled(True)
            replay_buttons(True, True)

    def replay_end(self, event, values):
        # Zurueck zum Livebild. Die Aufnahme bleibt fuer ein weiteres Replay
        # erhalten.
        self.replay_active = False
        blink_buttons(self.blink)
        timer_disabled(False)
        video_filter_disabled(self.blink)
        replay_buttons(True, False)
        self.video_resumed_at = time.monotonic()
        self.last_frame_id = -1

    # -- Bildanzeige -------------------------------------------------------

    def draw(self):
        if self.replay_active:
            self.draw_replay()
        elif self.display_video:
            self.draw_live()
        elif self.display_timer:
            self.draw_timer()

    def draw_replay(self):
        view = self.recording.view(time.monotonic() - self.replay_start)
        if view is None:
            # Nach dem roten Schlussbild endet das Replay von selbst, wie mit
            # "Replay Ende". replay_active sofort zuruecksetzen, damit das
            # Ereignis nur einmal gesendet wird.
            self.replay_active = False
            self.window.post('-REPLAY_END-')
            return
        segments = len(self.recording.windows)
        counter = replay.counter_text(view, segments)
        # Neu zeichnen nur, wenn sich Bild, Zaehler (Zehntelsekunden) oder
        # Zoom aendern.
        replay_key = (view.key, counter, self.zoom_level, tuple(self.zoom_center))
        if replay_key == self.last_replay_key:
            return
        self.last_replay_key = replay_key
        if view.kind == 'start':
            frame = replay_separator(self.video_size, timerlib.TITLES[self.replay_type])
        elif view.kind == 'separator':
            frame = replay_separator(self.video_size, f'Durchgang {view.segment + 1}/{segments}')
        elif view.kind == 'end':
            frame = replay_separator(self.video_size, '')
        elif view.frame is None:
            frame = replay_no_frame(self.video_size)
        else:
            frame = self.render(view.frame)
        self.window.draw_image(draw_replay_marker(frame, counter))
        self.window_fps.update('')

    def draw_live(self):
        # Nur neu zeichnen, wenn die Kamera ein neues Bild geliefert hat.
        frame_id, _, _ = self.cap.snapshot()
        frame = self.cap.getFrame() if frame_id != self.last_frame_id else None
        if frame is None:
            return
        self.last_frame_id = frame_id
        # Blinken: blink_ref ist das rohe, noch nicht entzerrte Bild. So
        # passt die Referenz nach einem Wechsel der Zoomstufe sofort zur
        # neuen Perspektive. Neu aufgenommen wird sie nur mit Start oder
        # Referenz, nie automatisch - sonst ginge der Vergleich alter und
        # neuer Treffer verloren.
        if self.blink and len(self.blink_ref) == 0:
            self.blink_ref = frame
        if self.blink and int(time.monotonic()) % 2 == 1:
            self.window.draw_image(self.render(self.blink_ref))
        else:
            self.window.draw_image(self.render(frame))
        self.update_fps()

    def update_fps(self):
        self.frame_count += 1
        now = time.monotonic()
        self.frame_timestamps.append(now)
        while self.frame_timestamps and (now - self.frame_timestamps[0]) > FPS_WINDOW_SECONDS:
            self.frame_timestamps.popleft()
        window_span = now - self.frame_timestamps[0]
        if window_span > 0:
            fps = len(self.frame_timestamps) / window_span
            self.window_fps.update(f'FPS: {round(fps, 1)} - Frame {self.frame_count}')

    def draw_timer(self):
        elapsed = time.monotonic() - self.timer_start
        # Aufnahme: jedes neue Kamerabild geht an die Aufnahme, die nur
        # behaelt, was in eine Gruenphase faellt.
        if not self.recording.is_complete(elapsed):
            frame_id, _, _ = self.cap.snapshot()
            if frame_id != self.last_rec_frame_id:
                self.last_rec_frame_id = frame_id
                frame = self.cap.getFrame()
                if frame is not None:
                    self.recording.add(frame, elapsed)
        state = timerlib.timer_state(self.timer_type, elapsed)
        # Nur neu zeichnen, wenn sich der Anzeigezustand aendert (hoechstens
        # einmal pro Sekunde).
        if state != self.last_timer_state:
            self.last_timer_state = state
            self.window.draw_image(timer_frame(self.video_size, state))
            self.window_fps.update('')
        if state.finished:
            self.window.post('-TIMER_STOP-')

    # -- Uhr ---------------------------------------------------------------

    def update_clock(self):
        # Datum und Uhrzeit nur neu zeichnen, wenn sich der Text aendert
        # (hoechstens einmal pro Sekunde statt bei jedem Schleifendurchlauf).
        now = datetime.now()
        date_str = now.strftime("%d.%m.%Y")
        if date_str != self.last_date_str:
            self.window_date.update(date_str)
            self.last_date_str = date_str
        time_str = now.strftime("%H:%M:%S")
        if time_str != self.last_time_str:
            self.window_time.update(time_str)
            self.last_time_str = time_str


def main():
    cfg = yamlconfig.load("config.yml")
    camera.log_version()
    video_size = (cfg.getProperty('video.size.x'), cfg.getProperty('video.size.y'))
    replay_settings = replay.read_settings(cfg)

    window = ui.window = ui.Window(cfg, video_size, version)
    show_page('-MAINVIEW-')
    # Wasserzeichen fuer "Video aus". Im Repo liegt ein Platzhalter, Ansible
    # ersetzt ihn optional durch das Vereinslogo (ansible/files/logo.png).
    # Fehlt die Datei, bleibt der Bildschirm bei "Video aus" schwarz.
    blank_logo = cv2.imread('ressources/logo.png', cv2.IMREAD_UNCHANGED)
    if blank_logo is None:
        print("ressources/logo.png nicht gefunden - Blank-Screen-Wasserzeichen bleibt leer.", file=sys.stderr)

    stands, cap, section_full, section_detail, settings_pin = run_setup_wizard(cfg)
    screen = MainScreen(window, video_size, cap, section_full, section_detail, stands,
                        settings_pin, replay_settings, blank_logo)
    show_page('-MAINVIEW-')

    while True:
        screen.check_stream()
        event, values = window.read(timeout=MAIN_LOOP_TICK_MS)
        if event == WIN_CLOSED:
            break
        screen.handle(event, values)
        screen.draw()
        screen.update_clock()
    window.close()


if __name__ == '__main__':
    process_handlers.install()
    main()
