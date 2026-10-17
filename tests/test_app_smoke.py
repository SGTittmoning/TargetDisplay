"""Rauchtest der gesamten Anwendung unter einem X-Server (Xvfb).

Startet main.main() mit einer Fake-Kamera und steuert die Oberflaeche wie ein
Bediener: Timer, Video aus/ein, PIN-Eingabe mit falscher und richtiger PIN,
Einstellungsmenue. Ausfuehren mit:  xvfb-run -a python -m pytest tests
"""
import importlib
import os
import sys
import threading
import time

import numpy as np
import pytest

tk = pytest.importorskip('tkinter')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

pytestmark = pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='kein X-Server (xvfb-run verwenden)')

CONFIG = """\
standName: Teststand
screenSize: [1024, 768]
settingsPin: "4711"
video:
  url: 'rtmp://test.invalid/live/test'
  section_full: [[100, 100], [500, 100], [500, 500], [100, 500]]
  section_detail: [[200, 200], [400, 200], [400, 400], [200, 400]]
  size:
    x: 600
    y: 600
"""


class FakeCamera:
    def __init__(self, url, **kwargs):
        self.url = url
        self.crop_region = None
        self.start_time = time.monotonic()
        self.last_frame_time = time.monotonic()
        self._t0 = time.monotonic()

    @property
    def frame_id(self):
        return 1 + int((time.monotonic() - self._t0) * 10)

    def getFrame(self, full=False):
        self.last_frame_time = time.monotonic()
        return np.full((700, 700, 3), 90, np.uint8)

    def snapshot(self):
        return self.frame_id, self.last_frame_time, self.start_time

    def set_crop_region(self, crop_region):
        self.crop_region = crop_region

    def is_stale(self, timeout, startup_timeout=None):
        return False

    def stop(self):
        pass


@pytest.fixture
def app(tmp_path, monkeypatch):
    (tmp_path / 'config.yml').write_text(CONFIG)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(ROOT)
    for name in ('main', 'ui', 'flows', 'settings_store'):
        sys.modules.pop(name, None)
    settings_store = importlib.import_module('settings_store')
    for attr in ('SECTIONS_OVERRIDE_FILE', 'STANDS_FILE', 'ACTIVE_STAND_FILE', 'PIN_FILE'):
        monkeypatch.setattr(settings_store, attr, str(tmp_path / 'nicht-vorhanden.json'))
    main = importlib.import_module('main')
    monkeypatch.setattr(main, 'Camera', FakeCamera)
    return main


class Driver(threading.Thread):
    """Schickt Bedienereignisse aus einem Hintergrund-Thread an die Oberflaeche."""

    def __init__(self, ui, steps):
        super().__init__(daemon=True)
        self.ui = ui
        self.steps = steps
        self.error = None

    def send(self, event):
        # Nur die Queue ist thread-sicher, Tk-Aufrufe aus einem anderen Thread
        # sind es nicht.
        self.ui.window.post(event)

    def run(self):
        try:
            for delay, event in self.steps:
                time.sleep(delay)
                self.send(event)
        except Exception as e:  # pragma: no cover
            self.error = e


def run_app(app, steps, monkeypatch, watchdog_sec=40):
    import ui
    draws = []
    real_draw = ui.Window.draw_image
    monkeypatch.setattr(ui.Window, 'draw_image', lambda self, frame: (draws.append(frame.copy()), real_draw(self, frame))[1])
    driver = Driver(ui, steps + [(0.3, ui.WIN_CLOSED)])

    def start_driver():
        while ui.window is None:
            time.sleep(0.05)
        driver.start()

    threading.Thread(target=start_driver, daemon=True).start()
    guard = threading.Timer(watchdog_sec, lambda: ui.window.post(ui.WIN_CLOSED))
    guard.daemon = True
    guard.start()
    try:
        app.main()
    finally:
        guard.cancel()
        ui.window = None
    assert driver.error is None
    return draws


def test_video_timer_und_video_aus(app, monkeypatch):
    steps = [
        (1.0, '-FULL_VIDEO-'),
        (0.3, '-DETAIL_VIDEO-'),
        (0.3, '-TOGGLEVIDEO-'),      # Video aus
        (0.5, '-TOGGLEVIDEO-'),      # Video ein
        (0.3, '-TIMER_10-'),         # Timer-Serie
        (2.5, '-TIMER_STOP-'),
        (0.5, '-BLINK_START-'),
        (1.0, '-BLINK_STOP-'),
    ]
    draws = run_app(app, steps, monkeypatch)
    assert len(draws) > 10
    # Die Timer-Anzeige ist rot (Vorbereitung): BGR (0, 0, 255)
    assert any((d[100, 100] == (0, 0, 255)).all() for d in draws)


def test_pin_falsch_dann_richtig_und_einstellungsmenue(app, monkeypatch):
    import flows
    from pinlock import PinLimiter
    limiter = PinLimiter()
    monkeypatch.setattr(limiter, 'register_failure', lambda: 0.3)
    monkeypatch.setattr(flows, 'PIN_LIMITER', limiter)
    opened = []
    real_settings = app.run_settings_flow
    monkeypatch.setattr(app, 'run_settings_flow', lambda *a, **k: (opened.append(True), real_settings(*a, **k))[1])
    steps = [
        (1.0, '-SETTINGS-'),
        (0.4, '1'), (0.1, '1'), (0.1, '1'), (0.1, '1'), (0.1, '-PIN_OK-'),   # falsch
        (0.2, '-PIN_CLEAR-'),
        (0.6, '4'), (0.1, '7'), (0.1, '1'), (0.1, '1'), (0.1, '-PIN_OK-'),   # richtig
        (0.6, '-MENU_BACK-'),
    ]
    run_app(app, steps, monkeypatch)
    assert opened == [True]   # das Einstellungsmenue wurde erst mit der richtigen PIN geoeffnet


def test_pin_abbrechen_waehrend_wartezeit(app, monkeypatch):
    import flows
    from pinlock import PinLimiter
    limiter = PinLimiter()
    monkeypatch.setattr(limiter, 'register_failure', lambda: 30)
    monkeypatch.setattr(flows, 'PIN_LIMITER', limiter)
    started = time.monotonic()
    steps = [
        (1.0, '-SETTINGS-'),
        (0.4, '1'), (0.1, '-PIN_OK-'),
        (0.5, '-PIN_CANCEL-'),   # waehrend der 30 s Wartezeit
    ]
    run_app(app, steps, monkeypatch)
    assert time.monotonic() - started < 15



class CountingCamera(FakeCamera):
    """Liefert je Frame einen anderen Grauwert, damit Referenz- und Live-Bild
    unterscheidbar sind."""

    def getFrame(self, full=False):
        self.last_frame_time = time.monotonic()
        return np.full((700, 700, 3), self.frame_id % 200, np.uint8)


def _button_states(monkeypatch, keys):
    """Haelt beim Schliessen des Fensters fest, welche Buttons bedienbar sind."""
    import ui
    states = {}
    real_close = ui.Window.close

    def close(self):
        for k in keys:
            states[k] = self[k].widget.cget('bg') == self._btn_style[k]['bg_on']
        real_close(self)

    monkeypatch.setattr(ui.Window, 'close', close)
    return states


def _is_timer_frame(d):
    return (d[100, 100] == (0, 0, 255)).all() or (d[100, 100] == (0, 255, 0)).all()


def test_timer_waehrend_blinken(app, monkeypatch):
    monkeypatch.setattr(app, 'Camera', CountingCamera)
    keys = ('-BLINK_START-', '-BLINK_REF-', '-BLINK_STOP-', '-TIMER_10-', '-TIMER_STOP-')
    states = _button_states(monkeypatch, keys)
    steps = [
        (1.0, '-BLINK_START-'),
        (0.5, '-TIMER_10-'),      # Timer trotz Blinken startbar
        (1.5, '-TIMER_STOP-'),
        (2.5, '-FULL_VIDEO-'),
    ]
    draws = run_app(app, steps, monkeypatch)
    timer_idx = [i for i, d in enumerate(draws) if _is_timer_frame(d)]
    assert timer_idx, 'Timer wurde nicht angezeigt'
    before = [int(d[300, 300, 0]) for d in draws[:timer_idx[0]]]
    after = [int(d[300, 300, 0]) for d in draws[timer_idx[-1] + 1:]]
    # Nach dem Timer blinkt das Bild mit der Referenz von VOR dem Timer
    # weiter: ein Teil der Bilder ist aelter als der Timer-Start.
    assert after and min(after) <= max(before)
    assert max(after) > max(before)
    # Blinken laeuft weiter, Timer wieder startbar
    assert states['-BLINK_STOP-'] and states['-BLINK_REF-']
    assert not states['-BLINK_START-']
    assert states['-TIMER_10-'] and not states['-TIMER_STOP-']


def test_timer_ohne_blinken_gibt_blinken_frei(app, monkeypatch):
    keys = ('-BLINK_START-', '-BLINK_REF-', '-BLINK_STOP-')
    states = _button_states(monkeypatch, keys)
    run_app(app, [(1.0, '-TIMER_10-'), (1.0, '-TIMER_STOP-')], monkeypatch)
    assert states['-BLINK_START-']
    assert not states['-BLINK_REF-'] and not states['-BLINK_STOP-']


def _is_replay_frame(d):
    from overlays import REPLAY_COLOR
    return d[d.shape[0] // 2, 3].tolist() == list(REPLAY_COLOR)


def test_replay_nach_timer(app, monkeypatch):
    import timerlib
    # Kurze Vorbereitungszeit, damit die Gruenphase im Test schnell beginnt.
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    keys = ('-REPLAY-', '-REPLAY_END-', '-TIMER_10-', '-FULL_VIDEO-', '-BLINK_START-')
    states = _button_states(monkeypatch, keys)
    steps = [
        (1.0, '-TIMER_10-'),
        (3.5, '-TIMER_STOP-'),     # ca. 1,5 s Gruen aufgenommen (Verzoegerung 1 s)
        (0.5, '-REPLAY-'),
        (0.5, '-DETAIL_VIDEO-'),   # Zoom im Replay
        (0.5, '-REPLAY-'),         # erneut abspielen
        (1.0, '-REPLAY_END-'),
        (0.8, '-FULL_VIDEO-'),
    ]
    draws = run_app(app, steps, monkeypatch)
    replay_idx = [i for i, d in enumerate(draws) if _is_replay_frame(d)]
    assert len(replay_idx) > 5
    # Nach "Replay Ende" wieder Livebild ohne Replay-Rahmen
    assert not _is_replay_frame(draws[-1])
    # Aufnahme bleibt nach "Replay Ende" fuer ein weiteres Replay erhalten
    assert states['-REPLAY-'] and not states['-REPLAY_END-']
    assert states['-TIMER_10-'] and states['-FULL_VIDEO-'] and states['-BLINK_START-']


def test_replay_endet_nach_rotem_schlussbild(app, monkeypatch):
    import timerlib
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    states = _button_states(monkeypatch, ('-REPLAY-', '-REPLAY_END-', '-TIMER_10-'))
    steps = [
        (1.0, '-TIMER_10-'),
        (3.0, '-TIMER_STOP-'),
        (0.3, '-REPLAY-'),
        (4.5, '-FULL_VIDEO-'),     # Replay (ca. 1 s + 1 s Rot) ist laengst vorbei
    ]
    draws = run_app(app, steps, monkeypatch)
    assert any(_is_replay_frame(d) for d in draws)
    assert not _is_replay_frame(draws[-1])
    assert states['-REPLAY-'] and not states['-REPLAY_END-'] and states['-TIMER_10-']


def test_replay_gesperrt_bei_video_aus(app, monkeypatch):
    import timerlib
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    states = _button_states(monkeypatch, ('-REPLAY-', '-REPLAY_END-'))
    steps = [
        (1.0, '-TIMER_10-'),
        (3.0, '-TIMER_STOP-'),
        (0.3, '-TOGGLEVIDEO-'),    # Video aus
        (0.3, '-REPLAY-'),         # wirkungslos, Button gesperrt
    ]
    draws = run_app(app, steps, monkeypatch)
    assert not any(_is_replay_frame(d) for d in draws)
    assert not states['-REPLAY-'] and not states['-REPLAY_END-']


def test_kein_replay_ohne_aufnahme(app, monkeypatch):
    states = _button_states(monkeypatch, ('-REPLAY-', '-REPLAY_END-'))
    run_app(app, [(1.0, '-FULL_VIDEO-')], monkeypatch)
    assert not states['-REPLAY-'] and not states['-REPLAY_END-']
