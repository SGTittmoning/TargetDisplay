"""Zustandslogik des Hauptbildschirms ohne Tk: Zoom (Viewport) und FPS-Anzeige
(FpsCounter)."""
from collections import deque

import cv2
import numpy as np

import transformlib as tl

# Schrittweite (Prozent) und Randzonen (Prozent der Bildbreite/-hoehe) beim
# Verschieben des manuellen Ausschnitts per Tippen.
PAN_STEP = 5
PAN_EDGE_LOW, PAN_EDGE_HIGH = 30, 70

# Zoomfelder im Bild: Kantenlaenge und Rand relativ zur Bildbreite, Farben (BGR).
CONTROL_SIZE_RATIO = 0.07
CONTROL_MARGIN_RATIO = 0.015
CONTROL_ALPHA, CONTROL_ALPHA_MUTED = 0.65, 0.35
CONTROL_BG = (40, 40, 40)
CONTROL_FG, CONTROL_FG_MUTED = (255, 255, 255), (170, 170, 170)

# Richtungsdreiecke am Bildrand, solange der Tipp-Zoom laeuft: Groesse und Rand
# in Pixel, bezogen auf ein Bild von 780 Pixeln Breite (skaliert mit der
# Bildbreite). Halbtransparent weiss mit schwarzem Rand.
TRIANGLE_W, TRIANGLE_H, TRIANGLE_INSET, TRIANGLE_OUTLINE = 100, 50, 16, 5
TRIANGLE_REFERENCE_WIDTH = 780
TRIANGLE_ALPHA = 0.55

# Anteil des Wegs von der gewaehlten Ansicht zur hoechsten Vergroesserung, den
# die erste Zoomstufe zuruecklegt.
STEP_SHARE = 0.5

# Toleranz beim Vergleich mit dem Rand in Prozent.
EDGE_EPS = 1e-6

# Zeitraum fuer die FPS-Anzeige.
FPS_WINDOW_SECONDS = 60


class Viewport:
    """Zoomstufe ('full'/'detail') und manueller Ausschnitt (Tipp-Zoom).

    Die Perspektiv-Matrizen bilden direkt auf video_size ab; sie aendern sich
    nur mit einem Neustart. Der Tipp-Zoom hat zwei Stufen: Stufe 1 liegt auf
    halbem Weg von der gewaehlten Ansicht zur hoechsten Vergroesserung
    tl.VIDEO_ZOOM_MAX, Stufe 2 erreicht sie. Beide Werte sind absolut auf
    "Ganze Scheibe" bezogen; detail_factor ist die Vergroesserung von "Innen
    Scheibe" dagegen und damit der Startwert dieser Ansicht.
    """

    def __init__(self, matrix_full, matrix_detail, video_size, detail_factor=1.0):
        self.matrices = {'full': matrix_full, 'detail': matrix_detail}
        self.video_size = video_size
        self.detail_factor = max(detail_factor, 1.0)
        self.level = 'full'
        # 0: kein Tipp-Zoom, 1: halber Weg zum Maximum, 2: Maximum.
        self.step = 0
        # Mitte des Tipp-Zooms in Prozent der Bildgroesse, None ohne Tipp-Zoom.
        self.center = None

    @property
    def key(self):
        """Wechselt genau dann, wenn sich die Ansicht aendert."""
        return self.level, self.step, self.center

    def start_factor(self):
        """Vergroesserung der gewaehlten Ansicht gegenueber "Ganze Scheibe"."""
        return self.detail_factor if self.level == 'detail' else 1.0

    def absolute_factor(self):
        """Vergroesserung der aktuellen Stufe gegenueber "Ganze Scheibe"."""
        start = self.start_factor()
        top = max(tl.VIDEO_ZOOM_MAX, start)
        return (start, start + STEP_SHARE * (top - start), top)[self.step]

    def factor(self):
        """Vergroesserung des Tipp-Zooms gegenueber dem Anzeigebild der
        aktuellen Ansicht."""
        return self.absolute_factor() / self.start_factor()

    def set_level(self, level):
        # Ganze/Innen Scheibe und Reset heben den Tipp-Zoom auf.
        self.level = level
        self.step = 0
        self.center = None

    def _zoom_to(self, step, center):
        self.step = step
        self.center = tl.clamp_zoom_center(center, self.factor()) if step else None

    def tap(self, x, y):
        """Tipp bei (x, y) in Prozent: der erste vergroessert um die Stelle,
        weitere nahe am Rand verschieben den Ausschnitt in diese Richtung."""
        if self.step == 0:
            self._zoom_to(1, (x, y))
            return
        cx, cy = self.center
        if x < PAN_EDGE_LOW:
            cx -= PAN_STEP
        elif x > PAN_EDGE_HIGH:
            cx += PAN_STEP
        if y < PAN_EDGE_LOW:
            cy -= PAN_STEP
        elif y > PAN_EDGE_HIGH:
            cy += PAN_STEP
        self._zoom_to(self.step, (cx, cy))

    def zoom_in(self):
        """Naechste Stufe; ohne Tipp-Zoom die normale Stufe um die Bildmitte."""
        self._zoom_to(min(self.step + 1, 2), self.center or (50, 50))

    def zoom_out(self):
        self._zoom_to(max(self.step - 1, 0), self.center)

    def render(self, raw):
        """Rohbild -> Anzeigebild: Entzerrung der Zoomstufe, dann der Tipp-Zoom.
        Gemeinsam fuer Livebild, Blinken und Replay."""
        out = cv2.warpPerspective(raw, self.matrices[self.level], self.video_size)
        if self.step:
            out = tl.crop(out, self.factor(), self.center)
        return out

    def _control_rects(self):
        """Felder "-" und "+" unten rechts im Bild (Pixel)."""
        width, height = self.video_size
        side = max(int(width * CONTROL_SIZE_RATIO), 24)
        margin = max(int(width * CONTROL_MARGIN_RATIO), 6)
        y0 = height - margin - side
        x_plus = width - margin - side
        x_minus = x_plus - side
        return {name: (x, y0, x + side, y0 + side) for name, x in (('out', x_minus), ('in', x_plus))}

    def pan_directions(self):
        """Richtungen ('left', 'right', 'up', 'down'), in die sich der Ausschnitt
        noch verschieben laesst; leer ohne Tipp-Zoom."""
        if not self.step:
            return []
        low = 100 / (2 * self.factor())
        high = 100 - low
        cx, cy = self.center
        return [name for name, free in (('left', cx - low > EDGE_EPS), ('right', high - cx > EDGE_EPS),
                                        ('up', cy - low > EDGE_EPS), ('down', high - cy > EDGE_EPS)) if free]

    def _triangle(self, direction):
        """Eckpunkte des Dreiecks am Rand in Richtung direction (Pixel)."""
        width, height = self.video_size
        scale = width / TRIANGLE_REFERENCE_WIDTH
        w, h, inset = TRIANGLE_W * scale, TRIANGLE_H * scale, TRIANGLE_INSET * scale
        cx, cy = width / 2, height / 2
        points = {
            'up': [(cx - w / 2, inset + h), (cx + w / 2, inset + h), (cx, inset)],
            'down': [(cx - w / 2, height - inset - h), (cx + w / 2, height - inset - h), (cx, height - inset)],
            'left': [(inset + h, cy - w / 2), (inset + h, cy + w / 2), (inset, cy)],
            'right': [(width - inset - h, cy - w / 2), (width - inset - h, cy + w / 2), (width - inset, cy)],
        }[direction]
        return np.array(points, np.int32)

    def _draw_triangles(self, out):
        directions = self.pan_directions()
        if not directions:
            return
        fill = out.copy()
        for direction in directions:
            cv2.fillPoly(fill, [self._triangle(direction)], (255, 255, 255))
        out[:] = cv2.addWeighted(fill, TRIANGLE_ALPHA, out, 1 - TRIANGLE_ALPHA, 0)
        thickness = max(int(TRIANGLE_OUTLINE * self.video_size[0] / TRIANGLE_REFERENCE_WIDTH), 1)
        for direction in directions:
            cv2.polylines(out, [self._triangle(direction)], True, (0, 0, 0), thickness, cv2.LINE_AA)

    def hit(self, x, y):
        """'in' oder 'out', wenn der Tipp bei (x, y) (Prozent) eines der beiden
        Felder trifft, sonst None."""
        px, py = x * self.video_size[0] / 100, y * self.video_size[1] / 100
        for name, (x0, y0, x1, y1) in self._control_rects().items():
            if x0 <= px < x1 and y0 <= py < y1:
                return name
        return None

    def render_with_controls(self, raw):
        """render() mit den Zoomfeldern im Bild ("-" ohne Tipp-Zoom und "+"
        beim Maximum sind blass) und den Richtungsdreiecken."""
        out = self.render(raw)
        self._draw_triangles(out)
        for name, (x0, y0, x1, y1) in self._control_rects().items():
            enabled = not ((name == 'out' and self.step == 0) or (name == 'in' and self.step == 2))
            roi = out[y0:y1, x0:x1]
            alpha = CONTROL_ALPHA if enabled else CONTROL_ALPHA_MUTED
            out[y0:y1, x0:x1] = (roi * (1 - alpha) + np.array(CONTROL_BG, np.float32) * alpha).astype(np.uint8)
            cx, cy, arm = (x0 + x1) // 2, (y0 + y1) // 2, (x1 - x0) // 4
            fg = CONTROL_FG if enabled else CONTROL_FG_MUTED
            cv2.line(out, (cx - arm, cy), (cx + arm, cy), fg, 3, cv2.LINE_AA)
            if name == 'in':
                cv2.line(out, (cx, cy - arm), (cx, cy + arm), fg, 3, cv2.LINE_AA)
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
