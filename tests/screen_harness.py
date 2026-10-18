"""MainScreen ohne Tk und in simulierter Zeit: ein Ersatzfenster sammelt die
gezeichneten Bilder und Button-Zustaende, eine falsche Uhr und eine
Ersatzkamera laufen im Gleichschritt. So laufen Timer und Replay in
Millisekunden statt in Echtzeit."""
from collections import deque

import numpy as np

import main
import ui

# Ausschnitte im Kamerabild (Pixel) und Groesse des Anzeigebilds.
SECTION_FULL = [[0, 0], [119, 0], [119, 119], [0, 119]]
SECTION_DETAIL = [[30, 30], [89, 30], [89, 89], [30, 89]]
VIDEO_SIZE = (300, 300)

TICK = 1 / 30          # Takt der Hauptschleife
CAMERA_FPS = 6


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = start

    def __call__(self):
        return self.now


class FakeCamera:
    """Liefert CAMERA_FPS Bilder pro Sekunde simulierter Zeit. Jedes Bild hat
    einen anderen Grauwert (Bildnummer % 200), mit Verlauf in x, damit Zoom
    und Ausschnitt sichtbar werden."""

    def __init__(self, clock):
        self.clock = clock
        self.t0 = clock()
        self.crop = None
        # True: die Kamera liefert keine Bilder (get_frame gibt None zurueck).
        self.blank = False

    @property
    def frame_id(self):
        return 1 + int((self.clock() - self.t0) * CAMERA_FPS)

    def image(self):
        x = np.tile(np.arange(120, dtype=np.uint8), (120, 1))
        return np.dstack([np.full((120, 120), self.frame_id % 200, np.uint8), x, x])

    def get_frame(self, full=False):
        if self.blank:
            return None
        img = self.image()
        if self.crop is not None and not full:
            x0, y0, x1, y1 = self.crop
            img = img[y0:y1, x0:x1]
        return img.copy()

    def snapshot(self):
        return self.frame_id, self.clock(), self.t0

    def set_crop_region(self, crop):
        self.crop = crop

    def is_stale(self, timeout, startup_timeout=None):
        return False


class FakeElem:
    def __init__(self):
        self.value = None

    def update(self, value=None, **kwargs):
        if value is not None:
            self.value = value


class FakeWindow:
    def __init__(self):
        self.events = deque()
        self.draws = []
        self.controls = None
        self.elems = {}

    def __getitem__(self, key):
        return self.elems.setdefault(key, FakeElem())

    def post(self, key, values=None):
        self.events.append((key, values or {}))

    def draw_image(self, frame):
        self.draws.append(frame.copy())

    def apply_controls(self, mode, blink=False, zoom_level='full', zoom_center=None, has_recording=False):
        self.controls = dict(mode=mode, blink=blink, zoom_level=zoom_level, zoom_center=zoom_center,
                             has_recording=has_recording)

    def show_info(self, *args, **kwargs):
        pass


class Screen:
    """MainScreen mit Ersatzfenster, falscher Uhr und Ersatzkamera."""

    def __init__(self, monkeypatch, replay_settings=(1.0, 600 * 1024 * 1024)):
        self.clock = FakeClock()
        self.window = FakeWindow()
        monkeypatch.setattr(ui, 'window', self.window)
        self.cap = FakeCamera(self.clock)
        self.screen = main.MainScreen(self.window, VIDEO_SIZE, self.cap, SECTION_FULL, SECTION_DETAIL,
                                      [], '4711', replay_settings, None, clock=self.clock)

    def send(self, event, values=None):
        self.window.post(event, values)
        return self

    def run(self, seconds, each=None):
        """Hauptschleife fuer `seconds` simulierte Sekunden; `each` wird nach
        jedem Durchlauf aufgerufen."""
        end = self.clock.now + seconds
        while self.clock.now < end:
            while self.window.events:
                self.screen.handle(*self.window.events.popleft())
            self.screen.draw()
            if each:
                each()
            self.clock.now += TICK
        return self

    @property
    def mode(self):
        return self.screen.mode


def gray(frame):
    """Grauwert (Bildnummer) in der Bildmitte, Kanal 0."""
    return int(frame[frame.shape[0] // 2, frame.shape[1] // 2, 0])
