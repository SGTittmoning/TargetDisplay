import cv2
import numpy as np


def compute_perspective_matrix(pts, dsize):
    # 3x3-Homographie, die das Viereck pts (schraeg gesehene Scheibe) auf ein
    # Rechteck der Groesse dsize abbildet. Das korrigiert den Blickwinkel, nicht
    # die Objektivverzeichnung. MainScreen berechnet sie einmal, je Frame wird
    # sie nur mit warpPerspective() angewendet.
    rect = order_points(pts)
    dst = np.array([
        [0, 0],
        [dsize[0] - 1, 0],
        [dsize[0] - 1, dsize[1] - 1],
        [0, dsize[1] - 1]], dtype="float32")
    return cv2.getPerspectiveTransform(rect, dst)


def order_points(pts):
    # Sortiert vier Eckpunkte zu (oben links, oben rechts, unten rechts,
    # unten links). Die Reihenfolge ergibt sich aus dem Winkel um den
    # Schwerpunkt (in Bildkoordinaten, y nach unten, also im Uhrzeigersinn);
    # als erster Punkt gilt der mit der kleinsten Koordinatensumme x+y. Anders
    # als die verbreitete Summen-/Differenz-Heuristik liefert das auch bei um
    # ca. 45 Grad gedrehtem Viereck vier verschiedene Punkte.
    pts = np.asarray(pts, dtype="float32")
    center = pts.mean(axis=0)
    angles = np.arctan2(pts[:, 1] - center[1], pts[:, 0] - center[0])
    clockwise = pts[np.argsort(angles)]
    start = int(np.argmin(clockwise.sum(axis=1)))
    return np.roll(clockwise, -start, axis=0)


def is_valid_quad(pts, min_area=100.0):
    # True, wenn die vier Punkte ein konvexes Viereck mit mindestens min_area
    # Flaeche (Pixel^2) bilden - nur dann liefert getPerspectiveTransform eine
    # brauchbare Entzerrung (keine Ueberkreuzung, keine drei Punkte auf einer
    # Linie, keine doppelten Punkte).
    pts = np.asarray(pts, dtype="float64")
    if pts.shape != (4, 2):
        return False
    rect = order_points(pts).astype("float64")
    edges = np.roll(rect, -1, axis=0) - rect
    cross = edges[:, 0] * np.roll(edges, -1, axis=0)[:, 1] - edges[:, 1] * np.roll(edges, -1, axis=0)[:, 0]
    if not (np.all(cross > 0)):
        return False
    area = 0.5 * abs(np.sum(rect[:, 0] * np.roll(rect[:, 1], -1) - np.roll(rect[:, 0], -1) * rect[:, 1]))
    return area >= min_area


def crop_bounds(point_sets, margin):
    # Umschliessendes Rechteck (x0, y0, x1, y1) aller Punkte plus margin.
    # x0/y0 werden auf 0 begrenzt, x1/y1 begrenzt camera.py gegen die echte
    # Bildgroesse.
    all_pts = np.vstack(point_sets)
    min_xy = all_pts.min(axis=0)
    max_xy = all_pts.max(axis=0)
    x0 = max(0, int(min_xy[0]) - margin)
    y0 = max(0, int(min_xy[1]) - margin)
    x1 = int(max_xy[0]) + margin
    y1 = int(max_xy[1]) + margin
    return x0, y0, x1, y1


def crop(image, zoom_factor, center):
    height, width = image.shape[0], image.shape[1]

    # center ist (x %, y %) der Bildbreite bzw. -hoehe.
    center = int(width / 100 * center[0]), int(height / 100 * center[1])
    # offset ist die halbe Kantenlaenge des Ausschnitts.
    offset = int(width / (2 * zoom_factor)), int(height / (2 * zoom_factor))

    # Ausschnitt innerhalb des Bilds halten. clamp_zoom_center begrenzt die
    # Mitte schon in Prozent; beim Umrechnen in Pixel kann die Rundung sie aber
    # um ein Pixel ueber den Rand schieben, ein negativer Index schnitte dann
    # das falsche Stueck aus.
    if offset[0] > center[0]:
        center = offset[0], center[1]
    if offset[1] > center[1]:
        center = center[0], offset[1]
    if offset[0] + center[0] > width:
        center = width - offset[0], center[1]
    if offset[1] + center[1] > height:
        center = center[0], height - offset[1]

    # Ausschnitt um center herausschneiden (image[y1:y2, x1:x2]) und erst
    # dann auf die Originalgroesse hochskalieren - billiger, als das ganze Bild
    # zu vergroessern und das meiste zu verwerfen.
    image = image[center[1] - offset[1]:center[1] + offset[1], center[0] - offset[0]:center[0] + offset[0]]
    image = cv2.resize(image, (width, height))
    return image


# Hoechste Vergroesserung des manuellen Zooms (Tippen ins Bild, siehe crop()),
# absolut bezogen auf das Bild "Ganze Scheibe": das, was die Kamera noch sauber
# aufloest. Am Stand 1 ist Innen Scheibe 2,1-fach vergroessert; mit weiteren
# 3x ergibt das 6,3x. Die erste Stufe liegt auf halbem Weg von der gewaehlten
# Ansicht zu diesem Wert (siehe screenlogic.Viewport).
VIDEO_ZOOM_MAX = 6.3


def clamp_zoom_center(center, zoom_factor):
    # Begrenzt zoom_center (x %, y %) dauerhaft. crop() begrenzt nur fuer die
    # Anzeige; ohne diese Grenze wuerde zoom_center bei wiederholtem Tippen an
    # den Rand immer weiter wandern, und der Rueckweg braeuchte entsprechend
    # viele Tipps. Die Grenze haengt nur vom Zoomfaktor ab.
    lo = 100 / (2 * zoom_factor)
    hi = 100 - lo
    return (min(max(center[0], lo), hi), min(max(center[1], lo), hi))
