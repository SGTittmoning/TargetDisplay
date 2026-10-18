"""Zustandslogik des Hauptbildschirms ohne Tk: Zoom (Viewport) und FPS-Anzeige
(FpsCounter)."""
from collections import deque

import cv2

import transformlib as tl

# Schrittweite (Prozent) und Randzonen (Prozent der Bildbreite/-hoehe) beim
# Verschieben des manuellen Ausschnitts per Tippen.
PAN_STEP = 5
PAN_EDGE_LOW, PAN_EDGE_HIGH = 30, 70

# Zeitraum fuer die FPS-Anzeige.
FPS_WINDOW_SECONDS = 60


class Viewport:
    """Zoomstufe ('full'/'detail') und manueller Ausschnitt (Tipp-Zoom).

    Die Perspektiv-Matrizen bilden direkt auf video_size ab; sie aendern sich
    nur mit einem Neustart.
    """

    def __init__(self, matrix_full, matrix_detail, video_size):
        self.matrices = {'full': matrix_full, 'detail': matrix_detail}
        self.video_size = video_size
        self.level = 'full'
        # Mitte des Tipp-Zooms in Prozent der Bildgroesse, None ohne Tipp-Zoom.
        self.center = None

    @property
    def key(self):
        """Wechselt genau dann, wenn sich die Ansicht aendert."""
        return self.level, self.center

    def set_level(self, level):
        # Ganze/Innen Scheibe und Reset heben den Tipp-Zoom auf.
        self.level = level
        self.center = None

    def tap(self, x, y):
        """Tipp bei (x, y) in Prozent: der erste vergroessert um die Stelle,
        weitere nahe am Rand verschieben den Ausschnitt in diese Richtung."""
        if self.center is None:
            center = (x, y)
        else:
            cx, cy = self.center
            if x < PAN_EDGE_LOW:
                cx -= PAN_STEP
            elif x > PAN_EDGE_HIGH:
                cx += PAN_STEP
            if y < PAN_EDGE_LOW:
                cy -= PAN_STEP
            elif y > PAN_EDGE_HIGH:
                cy += PAN_STEP
            center = (cx, cy)
        self.center = tl.clamp_zoom_center(center)

    def render(self, raw):
        """Rohbild -> Anzeigebild: Entzerrung der Zoomstufe, dann der Tipp-Zoom.
        Gemeinsam fuer Livebild, Blinken und Replay."""
        out = cv2.warpPerspective(raw, self.matrices[self.level], self.video_size)
        if self.center is not None:
            out = tl.crop(out, tl.VIDEO_ZOOM_FACTOR, self.center)
        return out


class FpsCounter:
    """Bildzaehler und Bildrate ueber die letzten FPS_WINDOW_SECONDS."""

    def __init__(self):
        self.count = 0
        self._stamps = deque()

    def tick(self, now):
        """Zaehlt ein angezeigtes Bild; liefert den Anzeigetext oder None, solange
        noch keine Zeitspanne messbar ist."""
        self.count += 1
        self._stamps.append(now)
        while now - self._stamps[0] > FPS_WINDOW_SECONDS:
            self._stamps.popleft()
        span = now - self._stamps[0]
        if span <= 0:
            return None
        return f'FPS: {round(len(self._stamps) / span, 1)} - Frame {self.count}'
