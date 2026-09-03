import cv2
import numpy as np

def compute_perspective_matrix(pts, dsize):
  # Liefert die 3x3-Homographie-Matrix, die das per pts definierte Viereck
  # (perspektivische Ansicht der flachen Scheibe) auf ein dsize-grosses
  # Rechteck abbildet ("Keystone"-Entzerrung des Kamerawinkels - KEINE
  # Fischauge-/Objektivverzeichnungs-Korrektur, dafuer braeuchte es eine
  # eigene, radiale Entzerrung per cv2.undistort() mit einer per
  # Schachbrett-Kalibrierung ermittelten Kamera-Matrix, die es hier nicht
  # gibt). pts_full/pts_detail aendern sich zur Laufzeit nie (nur ein
  # Neustart nach einer Settings-Aenderung setzt sie neu) - die Matrix wird
  # deshalb bewusst einmalig in main() berechnet und pro Frame nur noch
  # mit cv2.warpPerspective() angewendet, statt sie (und die vorher
  # zusaetzlich noetige separate cv2.resize()-Skalierung auf VideoSize) bei
  # jedem einzelnen Frame neu zu berechnen.
  rect = order_points(pts)
  dst = np.array([
    [0, 0],
    [dsize[0] - 1, 0],
    [dsize[0] - 1, dsize[1] - 1],
    [0, dsize[1] - 1]], dtype = "float32")
  return cv2.getPerspectiveTransform(rect, dst)

def order_points(pts):
  # initialzie a list of coordinates that will be ordered
  # such that the first entry in the list is the top-left,
  # the second entry is the top-right, the third is the
  # bottom-right, and the fourth is the bottom-left
  rect = np.zeros((4, 2), dtype = "float32")
  # the top-left point will have the smallest sum, whereas
  # the bottom-right point will have the largest sum
  s = pts.sum(axis = 1)
  rect[0] = pts[np.argmin(s)]
  rect[2] = pts[np.argmax(s)]
  # now, compute the difference between the points, the
  # top-right point will have the smallest difference,
  # whereas the bottom-left will have the largest difference
  diff = np.diff(pts, axis = 1)
  rect[1] = pts[np.argmin(diff)]
  rect[3] = pts[np.argmax(diff)]
  # return the ordered coordinates
  return rect

  
def crop_bounds(point_sets, margin):
  # point_sets: Liste von Nx2-Punkt-Arrays (z.B. [pts_full, pts_detail])
  # liefert (x0, y0, x1, y1) - x0/y0 nach unten auf 0 geclamped,
  # x1/y1 werden vom Aufrufer/camera.py zur Laufzeit gegen die echte
  # Framegroesse geclamped, da die hier oft noch nicht bekannt ist.
  all_pts = np.vstack(point_sets)
  min_xy = all_pts.min(axis=0)
  max_xy = all_pts.max(axis=0)
  x0 = max(0, int(min_xy[0]) - margin)
  y0 = max(0, int(min_xy[1]) - margin)
  x1 = int(max_xy[0]) + margin
  y1 = int(max_xy[1]) + margin
  return x0, y0, x1, y1

def crop(cv2Object, zoomSize, center):
    height, width = cv2Object.shape[0], cv2Object.shape[1]

    # center is (x%, y%): x scales with width, y scales with height
    center = int(width / 100 * center[0]), int(height / 100 * center[1])
    # offset is the half-extent of the crop box: x-extent from width, y-extent from height
    offset = int(width / (2*zoomSize)), int(height / (2*zoomSize))

    # check if out of bound
    if (offset[0] > center[0]): center=offset[0],center[1]
    if (offset[1] > center[1]): center=center[0],offset[1]
    if ((offset[0] + center[0]) > width): center=(width - offset[0]),center[1]
    if ((offset[1] + center[1]) > height): center=center[0],(height - offset[1])

    # The image/video frame is cropped to the center with a size of the original picture
    # image[y1:y2,x1:x2] is used to iterate and grab a portion of an image
    # (y1,x1) is the top left corner and (y2,x1) is the bottom right corner of new cropped frame.
    cv2Object = cv2Object[center[1]-offset[1]:center[1] + offset[1], center[0]-offset[0]:center[0] + offset[0]]
    # scale the small cropped window up to the target size now, instead of
    # upscaling the whole frame first and immediately discarding most of it
    cv2Object = cv2.resize(cv2Object, (width, height))
    return cv2Object
