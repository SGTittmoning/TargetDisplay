import os
import sys
import time
from collections import namedtuple
from datetime import datetime

import cv2
import numpy as np

import camera
import process_handlers
import replay
import sysinfo
import timerlib
import transformlib as tl
import ui
import yamlconfig
from camera import STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC, Camera
from flows import (
    SECTION_LABELS,
    change_pin_flow,
    check_pin,
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
from screenlogic import FpsCounter, Viewport
from section_editor import edit_section_points
from settings_store import (
    DEFAULT_PIN,
    load_active_stand,
    load_sections,
    load_settings_pin,
    load_stands,
    save_active_stand,
    save_sections_override,
)
from ui import WIN_CLOSED, apply_controls, popup, set_stand_name, show_info, show_page
from watchdog import stale_check_due

# Takt der Hauptschleife (Neuzeichnen, Timer, Uhr). Tasteneingaben wecken
# window.read() sofort, der Takt bestimmt nur, wie schnell ein neuer
# Kamera-Frame (ca. 6 pro Sekunde) oder ein Timer-Schritt auf dem Bildschirm
# erscheint.
MAIN_LOOP_TICK_MS = 33

# Blinken: Referenz und Livebild wechseln im Abstand von so vielen Sekunden.
BLINK_SWITCH_SEC = 1

# Rand um die beiden Ausschnitte beim Zuschnitt des Kamerabilds (Pixel).
CROP_MARGIN = 15

VERSION = '0.13.8'


# Ergebnis des Assistenten: alles, was der Hauptbildschirm zum Start braucht.
Setup = namedtuple('Setup', 'stands cap section_full section_detail settings_pin')


LOGO_CUSTOM = 'ressources/logo-custom.png'
LOGO_DEFAULT = 'ressources/logo.png'


def load_logo():
    # Wasserzeichen fuer "Video aus". Ansible legt das Vereinslogo als eigene,
    # nicht versionierte Datei ab; sie hat Vorrang vor dem Platzhalter im Repo.
    # Fehlt beides, bleibt der Bildschirm bei "Video aus" schwarz.
    for path in (LOGO_CUSTOM, LOGO_DEFAULT):
        # Ohne Vorpruefung meldet OpenCV jede fehlende Datei im Journal.
        logo = cv2.imread(path, cv2.IMREAD_UNCHANGED) if os.path.exists(path) else None
        if logo is not None:
            return logo
    print(f"{LOGO_DEFAULT} nicht gefunden - Blank-Screen-Wasserzeichen bleibt leer.", file=sys.stderr)
    return None


def run_setup_wizard(cfg):
    """Ersteinrichtungs-Assistent: Stand, Ausschnitte, PIN.

    Jeder Schritt erscheint nur, solange seine Einstellung fehlt, und laesst
    sich nicht abbrechen. Gespeichert wird jeder Schritt sofort auf
    /boot/firmware: nach einem Stromausfall geht es beim naechsten Start
    genau mit dem fehlenden Schritt weiter.

    Liefert ein Setup.
    """
    stands = load_stands()

    def choose_stand():
        # Pflichtauswahl, bis ein Stand gespeichert ist.
        nonlocal stands
        while True:
            stands = load_stands()
            chosen = run_stand_select(stands, forced=True)
            if chosen is None:
                continue
            if save_active_stand(chosen['id']):
                return chosen
            popup('Stand konnte nicht gespeichert werden.')

    active_stand = load_active_stand(stands, cfg)
    if active_stand is None:
        active_stand = choose_stand()
    set_stand_name(active_stand['displayName'])

    # Zunaechst ohne Zuschnitt: die Kalibrierung braucht das volle Bild. Der
    # Zuschnitt wird spaeter am laufenden Objekt gesetzt, ohne neue Verbindung.
    cap = Camera(active_stand['url'])

    section_full, section_detail = load_sections(cfg)
    while section_full is None or section_detail is None:
        if wait_for_camera_frame(cap) is None:
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
            if not save_sections_override(*with_section(which, result, section_full, section_detail)):
                popup('Speichern fehlgeschlagen - Ausschnitt wurde NICHT übernommen.')
            section_full, section_detail = load_sections(cfg)

    settings_pin = load_settings_pin(cfg)
    while settings_pin == DEFAULT_PIN:
        new_pin = change_pin_flow(forced=True)
        if new_pin is not None:
            settings_pin = new_pin
    return Setup(stands, cap, section_full, section_detail, settings_pin)


class MainScreen:
    """Zustand und Ablauf des Hauptbildschirms: Bedienereignisse
    (handle), Bildanzeige je Modus (draw), Stream-Ueberwachung
    (check_stream) und Uhr (update_clock)."""

    def __init__(self, window, video_size, cap, section_full, section_detail, stands,
                 settings_pin, replay_settings, blank_logo, clock=time.monotonic):
        self.window = window
        # Monotone Uhr fuer Timer, Replay, Blinken und Stream-Ueberwachung; in
        # Tests ersetzbar.
        self.clock = clock
        self.video_size = video_size
        self.cap = cap
        self.section_full = section_full
        self.section_detail = section_detail
        self.stands = stands
        self.settings_pin = settings_pin
        self.replay_latency, self.replay_budget = replay_settings
        self.blank_logo = blank_logo

        # 'live' (Kamerabild), 'blank' (Video aus), 'timer' oder 'replay'.
        self.mode = 'live'
        self.video_resumed_at = self.clock()
        self.blink = False
        # Rohes Referenzbild beim Blinken, None bis zum naechsten Livebild.
        self.blink_ref = None
        self.last_frame_id = -1
        self.fps = FpsCounter()
        self.timer_type = None
        self.timer_started_at = self.clock()
        self.last_timer_state = None
        # Aufnahme der Gruenphasen der letzten Timer-Serie (replay.py). Bleibt
        # nach "Replay Ende" erhalten und wird erst beim Start der naechsten
        # Serie verworfen.
        self.recording = None
        self.last_rec_frame_id = -1
        self.replay_started_at = self.clock()
        self.last_replay_key = None
        self.last_date_str = None
        self.last_time_str = None
        # FPS und Systemwerte im Fussbereich, ein-/ausgeblendet per Tipp.
        self.info_visible = False
        self.cpu_usage = sysinfo.CpuUsage()

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
        self.view = Viewport(tl.compute_perspective_matrix(pts_full - [x0, y0], video_size),
                             tl.compute_perspective_matrix(pts_detail - [x0, y0], video_size),
                             video_size, detail_factor=self.detail_magnification(pts_full, pts_detail))

        self._handlers = {
            '-TOGGLEVIDEO-': self.toggle_video,
            '-SETTINGS-': self.open_settings,
            '-FULL_VIDEO-': self.zoom_full,
            # Reset setzt Zoomstufe und manuellen Ausschnitt zurueck, wirkt
            # also wie "Ganze Scheibe".
            '-RESETZOOM-': self.zoom_full,
            '-DETAIL_VIDEO-': self.zoom_detail,
            '-VIDEO-': self.tap_video,
            # Start und Referenz nehmen beide ein neues Referenzbild auf.
            '-BLINK_START-': self.blink_start,
            '-BLINK_REF-': self.blink_start,
            '-BLINK_STOP-': self.blink_stop,
            '-TIMER_STOP-': self.timer_stop,
            '-REPLAY-': self.replay_start_clicked,
            '-REPLAY_END-': self.replay_end,
            '-INFO-': self.toggle_info,
        }
        for key in timerlib.TIMER_KEYS:
            self._handlers[key] = self.timer_start_clicked
        self.update_controls()
        self.update_info()

    # -- Hilfen ------------------------------------------------------------

    def has_recording(self):
        return self.recording is not None and self.recording.has_frames()

    def update_controls(self):
        apply_controls(self.mode, blink=self.blink, zoom_level=self.view.level,
                       zoom_center=self.view.center, has_recording=self.has_recording())

    # -- Stream-Ueberwachung -----------------------------------------------

    def check_stream(self):
        # Nur im Livebild pruefen (siehe stale_check_due). camera.py verbindet
        # sich im Hintergrund selbst neu.
        if not (stale_check_due(self.mode, self.clock(), self.video_resumed_at, STREAM_STALE_TIMEOUT_SEC)
                and self.cap.is_stale(STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC)):
            return
        # snapshot() liefert die Werte aus einem einzigen Lock-Zugriff, sie
        # stammen also aus demselben Moment.
        frame_id, last_frame_time, start_time = self.cap.snapshot()
        if frame_id == 0:
            print(f"Kein Kamera-Frame innerhalb der Startup-Frist von {STREAM_STARTUP_TIMEOUT_SEC}s erhalten "
                  f"(seit Prozessstart: {self.clock() - start_time:.1f}s) - beende Prozess fuer Neustart.", file=sys.stderr)
        else:
            print(f"Kein neuer Kamera-Frame seit {self.clock() - last_frame_time:.1f}s "
                  f"(Schwelle {STREAM_STALE_TIMEOUT_SEC}s, zuletzt frame_id={frame_id}) - beende Prozess fuer Neustart.", file=sys.stderr)
        # Eigener Exit-Code 2 (Stream-Ausfall), im Journal als
        # "TargetDisplay beendet (exit 2)" von play_it sichtbar.
        sys.exit(2)

    # -- Bedienereignisse --------------------------------------------------

    def handle(self, event, values):
        # Die Handler aendern nur den Zustand; welche Buttons danach bedienbar
        # sind, ergibt sich daraus an einer Stelle (update_controls).
        handler = self._handlers.get(event)
        if handler is not None:
            handler(event, values)
            self.update_controls()

    def toggle_video(self, event, values):
        if self.mode == 'blank':
            # Der FPS-Zaehler laeuft weiter, die FPS-Anzeige
            # pausiert nur (wie beim Timer).
            self.mode = 'live'
            self.video_resumed_at = self.clock()
        elif self.mode == 'live':
            self.mode = 'blank'
            self.window.draw_image(blank_frame(self.video_size, self.blank_logo))
            # FPS-Anzeige nur leeren, die Zaehler bleiben (siehe oben).
            self.window_fps.update('')

    def open_settings(self, event, values):
        # Die Sperren in ui.apply_controls verhindern das Ereignis im Timer und
        # im Replay; die Pruefung hier faengt eines ab, das trotzdem ankommt
        # (wie bei allen Handlern, die nur in bestimmten Modi gelten).
        if self.mode not in ('live', 'blank'):
            return
        if check_pin(self.settings_pin):
            if run_settings_flow(self.cap, self.section_full, self.section_detail, self.stands):
                # Gespeichert: Prozess beenden (Exit 0), systemd startet
                # ihn mit den neuen Werten neu.
                sys.exit(0)

    def toggle_info(self, event, values):
        self.info_visible = not self.info_visible
        # Erster Wert der CPU-Auslastung erst nach einer Sekunde Messzeit.
        self.cpu_usage.percent()
        self.update_info()

    def update_info(self):
        if not self.info_visible:
            show_info(False)
            return
        temp = sysinfo.cpu_temperature()
        show_info(True,
                  usage=sysinfo.usage_text(self.cpu_usage.percent(), sysinfo.ram_percent()),
                  temperature=sysinfo.temperature_text(temp),
                  level=sysinfo.temperature_level(temp),
                  load=sysinfo.load_text(sysinfo.load_average()))

    def zoom_full(self, event, values):
        self.view.set_level('full')

    def zoom_detail(self, event, values):
        self.view.set_level('detail')

    @staticmethod
    def detail_magnification(pts_full, pts_detail):
        # Vergroesserung von "Innen Scheibe" gegenueber "Ganze Scheibe": Wurzel
        # des Flaechenverhaeltnisses der beiden Ausschnitte im Kamerabild.
        full = cv2.contourArea(cv2.convexHull(pts_full.astype(np.int32)))
        detail = cv2.contourArea(cv2.convexHull(pts_detail.astype(np.int32)))
        return (full / detail) ** 0.5 if full > 0 and detail > 0 else 1.0

    def tap_video(self, event, values):
        # Zoomen nur, solange ein Bild zu sehen ist: im Timer und bei
        # "Video aus" bliebe ein Tipp sonst unbemerkt bis zum naechsten
        # Livebild stehen.
        if self.mode not in ('live', 'replay'):
            return
        x, y = values['-VIDEO-']
        hit = self.view.hit(x, y)
        if hit == 'in':
            self.view.zoom_in()
        elif hit == 'out':
            self.view.zoom_out()
        else:
            self.view.tap(x, y)

    def blink_start(self, event, values):
        # Das naechste Livebild wird zur Referenz. Zoom bleibt bedienbar, er
        # wirkt auf Referenz und Livebild gleich. Die Timer bleiben startbar
        # (siehe timer_start_clicked).
        self.blink_ref = None
        self.blink = True

    def blink_stop(self, event, values):
        self.blink_ref = None
        self.blink = False

    def timer_start_clicked(self, event, values):
        # Alle drei Timer-Varianten starten identisch - nur timer_type
        # (=event) unterscheidet, welcher Ablauf (timerlib) gerendert wird.
        # Ein laufendes Blinken pausiert nur: blink und blink_ref bleiben
        # unveraendert, nach dem Timer blinkt das Bild mit der Referenz von
        # vor der Serie weiter und zeigt so die Treffer der Serie.
        if self.mode != 'live':
            return
        self.mode = 'timer'
        self.timer_type = event
        self.timer_started_at = self.clock()
        self.last_timer_state = None
        # Die Aufnahme der vorigen Serie zuerst freigeben, dann die neue
        # anlegen - sonst lagen kurzzeitig beide im RAM.
        self.recording = None
        self.recording = replay.Recording(timerlib.green_windows(event), self.replay_latency, self.replay_budget)
        self.last_rec_frame_id, _, _ = self.cap.snapshot()

    def timer_stop(self, event, values):
        if self.mode != 'timer':
            return
        self.mode = 'live'
        self.video_resumed_at = self.clock()
        if self.recording is not None:
            print(f"Replay-Aufnahme: {self.recording.frame_count()} Bilder, "
                  f"{self.recording.bytes / 1024 / 1024:.0f} MB, jedes {self.recording.step}. Bild", file=sys.stderr)

    def replay_start_clicked(self, event, values):
        # Startet das Replay von vorn. Waehrend des Replays sendet derselbe
        # Button "-REPLAY_END-". Blinken pausiert (Zustand und Referenz
        # bleiben). Die Sperren stehen in ui.apply_controls(); die Pruefung
        # hier faengt zusaetzlich ein Ereignis ab, das trotz Sperre ankommt.
        if self.has_recording() and self.mode == 'live':
            self.mode = 'replay'
            self.replay_started_at = self.clock()
            self.last_replay_key = None

    def replay_end(self, event, values):
        # Zurueck zum Livebild. Die Aufnahme bleibt fuer ein weiteres Replay
        # erhalten.
        if self.mode != 'replay':
            return
        self.mode = 'live'
        self.video_resumed_at = self.clock()
        self.last_frame_id = -1

    # -- Bildanzeige -------------------------------------------------------

    def draw(self):
        if self.mode == 'replay':
            self.draw_replay()
        elif self.mode == 'live':
            self.draw_live()
        elif self.mode == 'timer':
            self.draw_timer()

    def draw_replay(self):
        view = self.recording.view(self.clock() - self.replay_started_at)
        if view is None:
            # Nach dem roten Schlussbild endet das Replay von selbst, wie mit
            # "Replay Ende".
            self.replay_end(None, None)
            self.update_controls()
            return
        segments = len(self.recording.windows)
        counter = replay.counter_text(view, segments)
        # Neu zeichnen nur, wenn sich Bild, Zaehler (Zehntelsekunden) oder
        # Zoom aendern.
        replay_key = (view.key, counter, self.view.key)
        if replay_key == self.last_replay_key:
            return
        self.last_replay_key = replay_key
        if view.kind == 'start':
            frame = replay_separator(self.video_size, timerlib.TITLES[self.timer_type])
        elif view.kind == 'separator':
            frame = replay_separator(self.video_size, f'Durchgang {view.segment + 1}/{segments}')
        elif view.kind == 'end':
            frame = replay_separator(self.video_size, '')
        elif view.frame is None:
            frame = replay_no_frame(self.video_size)
        else:
            frame = self.view.render_with_controls(view.frame)
        self.window.draw_image(draw_replay_marker(frame, counter))
        self.window_fps.update('')

    def draw_live(self):
        # Nur neu zeichnen, wenn die Kamera ein neues Bild geliefert hat.
        frame_id, _, _ = self.cap.snapshot()
        frame = self.cap.get_frame() if frame_id != self.last_frame_id else None
        if frame is None:
            return
        self.last_frame_id = frame_id
        # Blinken: blink_ref ist das rohe, noch nicht entzerrte Bild. So
        # passt die Referenz nach einem Wechsel der Zoomstufe sofort zur
        # neuen Perspektive. Neu aufgenommen wird sie nur mit Start oder
        # Referenz, nie automatisch - sonst ginge der Vergleich alter und
        # neuer Treffer verloren.
        if self.blink and self.blink_ref is None:
            self.blink_ref = frame
        if self.blink and int(self.clock() / BLINK_SWITCH_SEC) % 2 == 1:
            self.window.draw_image(self.view.render_with_controls(self.blink_ref))
        else:
            self.window.draw_image(self.view.render_with_controls(frame))
        self.update_fps()

    def update_fps(self):
        text = self.fps.tick(self.clock())
        if text is not None:
            self.window_fps.update(text)

    def draw_timer(self):
        elapsed = self.clock() - self.timer_started_at
        # Aufnahme: jedes neue Kamerabild geht an die Aufnahme, die nur
        # behaelt, was in eine Gruenphase faellt.
        if not self.recording.is_complete(elapsed):
            frame_id, _, _ = self.cap.snapshot()
            if frame_id != self.last_rec_frame_id:
                self.last_rec_frame_id = frame_id
                frame = self.cap.get_frame()
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
            # Ende wie mit "Timer Stop", direkt statt ueber die Ereignis-Queue:
            # so wird es genau einmal ausgefuehrt.
            self.timer_stop(None, None)
            self.update_controls()

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
            # Systemwerte im selben Takt, nur wenn eingeblendet.
            if self.info_visible:
                self.update_info()


def main():
    cfg = yamlconfig.load("config.yml")
    camera.log_version()
    video_size = (cfg.get_property('video.size.x'), cfg.get_property('video.size.y'))
    replay_settings = replay.read_settings(cfg)

    window = ui.window = ui.Window(cfg, video_size, VERSION)
    show_page('-MAINVIEW-')
    blank_logo = load_logo()

    setup = run_setup_wizard(cfg)
    screen = MainScreen(window, video_size, setup.cap, setup.section_full, setup.section_detail,
                        setup.stands, setup.settings_pin, replay_settings, blank_logo)
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
