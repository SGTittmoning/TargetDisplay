"""Gemeinsames Geruest der Oberflaechen-Tests: startet main.main() unter Xvfb
mit einer Fake-Kamera und steuert die Oberflaeche ueber die Ereignis-Queue."""
import importlib
import os
import sys
import threading
import time

import numpy as np
import pytest

tk = pytest.importorskip('tkinter')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fuer Testmodule, die die Oberflaeche starten.
needs_display = pytest.mark.skipif(not os.environ.get('DISPLAY'), reason='kein X-Server (xvfb-run verwenden)')

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

    def get_frame(self, full=False):
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


def load_app(tmp_path, monkeypatch, config):
    (tmp_path / 'config.yml').write_text(config)
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(ROOT)
    for name in ('main', 'ui', 'flows', 'settings_store'):
        sys.modules.pop(name, None)
    settings_store = importlib.import_module('settings_store')
    for attr in ('SECTIONS_OVERRIDE_FILE', 'STANDS_FILE', 'ACTIVE_STAND_FILE', 'PIN_FILE'):
        monkeypatch.setattr(settings_store, attr, str(tmp_path / f'{attr.lower()}.json'))
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
                if callable(event):
                    # z.B. den Zustand der Fake-Kamera umschalten (kein Tk)
                    event()
                elif isinstance(event, tuple):
                    self.ui.window.post(*event)
                else:
                    self.send(event)
        except Exception as e:  # pragma: no cover
            self.error = e


# Bedienbare Buttons des Hauptbildschirms (fuer Messpunkte).
MAIN_BUTTONS = ('-FULL_VIDEO-', '-DETAIL_VIDEO-', '-RESETZOOM-',
                '-BLINK_START-', '-BLINK_REF-', '-BLINK_STOP-',
                '-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-', '-TIMER_STOP-',
                '-REPLAY-', '-TOGGLEVIDEO-')


def clickable(window, key):
    """True, wenn ein Klick auf den Button ein Ereignis ausloest. Prueft den
    echten Klickweg (invoke), nicht nur die Farbe."""
    posted = []
    window.post = lambda event, values=None: posted.append(event)
    try:
        window[key].widget.invoke()
    finally:
        del window.post
    return bool(posted)


def probe(window, draws):
    return {
        'clickable': {k: clickable(window, k) for k in MAIN_BUTTONS},
        'frame': draws[-1] if draws else None,
        'draws': len(draws),
        'replay_text': window._btn_style['-REPLAY-']['label'].cget('text'),
        'fps': window['-FPS-'].widget.cget('text'),
        'date': window['-DATE-'].widget.cget('text'),
        'time': window['-TIME-'].widget.cget('text'),
        'stand': window['-STANDNAME-'].widget.cget('text'),
        'video_icon': str(window['-TOGGLEVIDEO-'].widget.cget('image')),
    }


def run_app(app, steps, monkeypatch, watchdog_sec=40, probes=None):
    """Startet die App und schickt die Schritte (Wartezeit, Ereignis). Ein
    Ereignis '-PROBE-name-' haelt in `probes[name]` den Zustand genau in
    diesem Moment fest (siehe probe()), statt erst beim Schliessen."""
    import ui
    draws = []
    real_draw = ui.Window.draw_image
    monkeypatch.setattr(ui.Window, 'draw_image', lambda self, frame: (draws.append(frame.copy()), real_draw(self, frame))[1])
    real_read = ui.Window.read

    def read(self, timeout=None):
        event, values = real_read(self, timeout)
        if isinstance(event, str) and event.startswith('-PROBE-'):
            probes[event[len('-PROBE-'):-1]] = probe(self, draws)
            return ui.TIMEOUT_EVENT, {}
        return event, values

    monkeypatch.setattr(ui.Window, 'read', read)
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
        # Bei sys.exit() aus main() bleibt das Fenster offen.
        if ui.window is not None:
            try:
                ui.window.close()
            except tk.TclError:
                pass
        ui.window = None
    assert driver.error is None
    return draws


class CountingCamera(FakeCamera):
    """Liefert je Frame einen anderen Grauwert, damit Referenz- und Live-Bild
    unterscheidbar sind."""

    def get_frame(self, full=False):
        self.last_frame_time = time.monotonic()
        return np.full((700, 700, 3), self.frame_id % 200, np.uint8)


def button_states(monkeypatch, keys):
    """Haelt beim Schliessen des Fensters fest, welche Buttons bedienbar sind."""
    import ui
    states = {}
    real_close = ui.Window.close

    def close(self):
        for k in keys:
            states[k] = self[k].widget.cget('bg') == self._btn_style[k]['bg_on']
        states['replay_text'] = self._btn_style['-REPLAY-']['label'].cget('text')
        real_close(self)

    monkeypatch.setattr(ui.Window, 'close', close)
    return states


def is_timer_frame(d):
    return (d[100, 100] == (0, 0, 255)).all() or (d[100, 100] == (0, 255, 0)).all()


def is_replay_frame(d):
    from overlays import REPLAY_COLOR
    return d[d.shape[0] // 2, 3].tolist() == list(REPLAY_COLOR)
