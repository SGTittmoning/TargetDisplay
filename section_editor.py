"""Ausschnitte-Editor: vier Eckpunkte eines Ausschnitts auf dem Kamerabild
setzen (Einstellungen-Menue und Ersteinrichtungs-Assistent)."""
import math
import tkinter as tk

import cv2

import transformlib as tl
import ui
from ui import EDITOR_MAX_H, EDITOR_MAX_W, WIN_CLOSED, popup, show_page

# Ausschnitte-Editor: Trefferbereich fuer Finger (Display-Pixel), Seitenlaenge
# der Lupe und des darin vergroesserten Kamera-Ausschnitts (Pixel), Abstand
# der Lupe zum Canvas-Rand.
HIT_RADIUS = 35
MAG_SIZE = 220
MAG_SRC = 70
MAG_MARGIN = 10


class CanvasMapping:
    """Kamerabild im Editor-Canvas: verkleinert (nie vergroessert) und mittig
    in die feste Flaeche EDITOR_MAX_W x EDITOR_MAX_H gesetzt."""

    def __init__(self, img_w, img_h):
        self.scale = min(EDITOR_MAX_W / img_w, EDITOR_MAX_H / img_h, 1.0)
        self.disp_w, self.disp_h = max(1, int(img_w * self.scale)), max(1, int(img_h * self.scale))
        self.off_x = (EDITOR_MAX_W - self.disp_w) // 2
        self.off_y = (EDITOR_MAX_H - self.disp_h) // 2

    def to_canvas(self, p):
        return [p[0] * self.scale + self.off_x, p[1] * self.scale + self.off_y]

    def to_image(self, p):
        return [(p[0] - self.off_x) / self.scale, (p[1] - self.off_y) / self.scale]

    def clamp(self, pos):
        # Auf den sichtbaren Bildbereich; ausserhalb liegt nur der schwarze
        # Rand ohne Bildkoordinate.
        return [min(max(pos[0], self.off_x), self.off_x + self.disp_w),
                min(max(pos[1], self.off_y), self.off_y + self.disp_h)]

    def display_frame(self, frame):
        return cv2.resize(frame, (self.disp_w, self.disp_h)) if self.scale != 1.0 else frame.copy()


def default_quad(img_w, img_h):
    # Startwert ohne Kalibrierung (Assistent): zentriertes Rechteck.
    mx, my = int(img_w * 0.2), int(img_h * 0.2)
    return [[mx, my], [img_w - mx, my], [mx, img_h - my], [img_w - mx, img_h - my]]


def nearest_point(pts, pos):
    """Index des Punkts innerhalb von HIT_RADIUS, der pos am naechsten liegt,
    oder None."""
    best_i, best_d = None, HIT_RADIUS
    for i, p in enumerate(pts):
        d = math.hypot(p[0] - pos[0], p[1] - pos[1])
        if d < best_d:
            best_i, best_d = i, d
    return best_i


def magnifier_image(frame, center):
    """Vergroesserter Ausschnitt MAG_SRC x MAG_SRC um center (Bildkoordinaten)
    mit Fadenkreuz. Am Bildrand wird per BORDER_REPLICATE aufgefuellt: so
    bleibt das Fadenkreuz auf dem Punkt, ein Beschneiden ergaebe einen
    verzerrten Ausschnitt. None, wenn center ganz ausserhalb liegt."""
    img_h, img_w = frame.shape[:2]
    half = MAG_SRC // 2
    x0, y0 = int(center[0] - half), int(center[1] - half)
    x1, y1 = x0 + MAG_SRC, y0 + MAG_SRC
    src_x0, src_y0 = max(0, x0), max(0, y0)
    src_x1, src_y1 = min(img_w, x1), min(img_h, y1)
    crop = frame[src_y0:src_y1, src_x0:src_x1]
    if crop.size == 0:
        return None
    pads = (src_y0 - y0, y1 - src_y1, src_x0 - x0, x1 - src_x1)
    if any(pads):
        crop = cv2.copyMakeBorder(crop, *pads, cv2.BORDER_REPLICATE)
    mag = cv2.resize(crop, (MAG_SIZE, MAG_SIZE), interpolation=cv2.INTER_NEAREST)
    cv2.line(mag, (MAG_SIZE // 2, 0), (MAG_SIZE // 2, MAG_SIZE), (0, 255, 255), 1)
    cv2.line(mag, (0, MAG_SIZE // 2), (MAG_SIZE, MAG_SIZE // 2), (0, 255, 255), 1)
    return mag


def magnifier_corner(point):
    """Linke obere Ecke der Lupe: die Canvas-Ecke, die vom gezogenen Punkt
    (Canvas-Koordinaten) am weitesten entfernt ist, damit sie ihn nicht
    verdeckt."""
    x = MAG_MARGIN if point[0] >= EDITOR_MAX_W / 2 else EDITOR_MAX_W - MAG_SIZE - MAG_MARGIN
    y = MAG_MARGIN if point[1] >= EDITOR_MAX_H / 2 else EDITOR_MAX_H - MAG_SIZE - MAG_MARGIN
    return x, y


def _ppm_photo(graph, image):
    # PPM statt PNG: unkomprimiert ist billiger, das Bild wird sofort wieder
    # dekodiert. Die Referenz in graph.image_refs haelt es am Leben.
    photo = tk.PhotoImage(data=cv2.imencode('.ppm', image)[1].tobytes())
    graph.image_refs.append(photo)
    return photo


def _draw_outline(graph, poly_pts, color):
    # Die Punkte haben keine feste Umlaufrichtung; 1-2-3-4-1 verbunden ergaebe
    # evtl. ein ueberkreuztes Viereck. Fuer den Umriss werden sie deshalb nach
    # Winkel um ihren Mittelpunkt sortiert, ihre Nummerierung bleibt.
    cx = sum(p[0] for p in poly_pts) / 4
    cy = sum(p[1] for p in poly_pts) / 4
    perimeter = sorted(range(4), key=lambda i: math.atan2(poly_pts[i][1] - cy, poly_pts[i][0] - cx))
    for j in range(4):
        a, b = poly_pts[perimeter[j]], poly_pts[perimeter[(j + 1) % 4]]
        graph.create_line(a[0], a[1], b[0], b[1], fill=color, width=2)


def _draw_points(graph, pts):
    # Rote Punkte mit gelber Nummer darueber.
    for i, p in enumerate(pts):
        graph.create_oval(p[0] - 10, p[1] - 10, p[0] + 10, p[1] + 10,
                           fill='red', outline='yellow', width=2)
        graph.create_text(p[0], p[1] - 20, text=str(i + 1), fill='yellow',
                           font=('Helvetica', 12, 'bold'))


def _draw_magnifier(graph, frame, mapping, point):
    # Lupe zum gerade gezogenen Punkt (Canvas-Koordinaten).
    mag = magnifier_image(frame, mapping.to_image(point))
    if mag is None:
        return
    mag_x, mag_y = magnifier_corner(point)
    graph.create_image(mag_x, mag_y, anchor='nw', image=_ppm_photo(graph, mag))
    graph.create_rectangle(mag_x, mag_y, mag_x + MAG_SIZE, mag_y + MAG_SIZE, outline='yellow', width=2)


def edit_section_points(region_label, cap, points, other_points=None, allow_cancel=True, will_restart=True):
    # points: 4 Punkte [x, y] in Kamerakoordinaten des vollen Bilds, oder None
    # (Assistent, noch keine Kalibrierung): dann dient default_quad() als
    # Startwert. get_frame(full=True) liefert das unbeschnittene Bild, damit
    # sich Ausschnitte auch ueber die bisherigen Grenzen hinaus setzen lassen.
    # Liefert beim Speichern die neuen Punkte, sonst None.
    #
    # will_restart bestimmt nur die Beschriftung des Speichern-Buttons: aus dem
    # Menue startet die App nach dem Speichern neu ("Speichern und neu
    # starten"), im Assistenten laeuft sie weiter ("Speichern").
    frame = cap.get_frame(full=True)
    if frame is None:
        popup('Kein Kamerabild verfügbar - bitte später erneut versuchen.')
        return None

    img_h, img_w = frame.shape[:2]
    mapping = CanvasMapping(img_w, img_h)
    disp_frame = mapping.display_frame(frame)
    # pts sind Canvas-Koordinaten, wie event.x/event.y beim Ziehen. Der andere
    # Ausschnitt wird zur Orientierung hellgrau mitgezeichnet, nicht verschiebbar.
    pts = [mapping.to_canvas(p) for p in (points if points is not None else default_quad(img_w, img_h))]
    other_pts = [mapping.to_canvas(p) for p in other_points] if other_points is not None else None

    show_page('-EDITORVIEW-')
    ui.window['-EDITOR_TITLE-'].update(f'{region_label}: Eckpunkte anpassen')
    ui.window['-EDIT_CANCEL-'].update(visible=allow_cancel)
    # Zweizeilig, sonst wird der Button zu breit.
    ui.window['-EDIT_SAVE-'].update('Speichern und\nneu starten' if will_restart else 'Speichern')
    graph = ui.window.editor_canvas

    def redraw(dragged=None):
        graph.delete('all')
        graph.image_refs = []
        graph.create_image(mapping.off_x, mapping.off_y, anchor='nw', image=_ppm_photo(graph, disp_frame))
        if other_pts is not None:
            _draw_outline(graph, other_pts, '#c0c0c0')
        _draw_outline(graph, pts, 'yellow')
        _draw_points(graph, pts)
        if dragged is not None:
            _draw_magnifier(graph, frame, mapping, dragged)

    redraw()
    dragging_idx = None

    while True:
        event, values = ui.window.read()
        if event in (WIN_CLOSED, '-EDIT_CANCEL-'):
            return None
        elif event == '-EDIT_REFRESH-':
            new_frame = cap.get_frame(full=True)
            if new_frame is not None:
                frame = new_frame
                disp_frame = mapping.display_frame(frame)
                redraw()
        elif event == '-EDIT_SAVE-':
            result = [mapping.to_image(p) for p in pts]
            # Ein ueberkreuztes, nicht konvexes oder zu kleines Viereck ergaebe
            # eine unbrauchbare Perspektiv-Entzerrung (leeres/verzerrtes Bild).
            if not tl.is_valid_quad(result):
                popup('Die vier Punkte bilden kein gültiges Viereck.\nBitte die Ecken so setzen, dass ein Rechteck bzw. Trapez entsteht.')
                continue
            return result
        elif event == '-EDITGRAPH-':
            pos = values['-EDITGRAPH-']
            if dragging_idx is None:
                dragging_idx = nearest_point(pts, pos)
            if dragging_idx is not None:
                pts[dragging_idx] = mapping.clamp(pos)
                redraw(dragged=pts[dragging_idx])
        elif event == '-EDITGRAPH_UP-':
            dragging_idx = None
            redraw()
