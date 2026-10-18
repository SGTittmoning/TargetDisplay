import numpy as np
import pytest

import transformlib as tl

BASE = [[100, 100], [400, 100], [400, 300], [100, 300]]
TRAPEZ = [[150, 100], [350, 100], [420, 300], [80, 300]]


def rotate(pts, deg):
    t = np.radians(deg)
    pts = np.array(pts, float)
    c = pts.mean(axis=0)
    r = np.array([[np.cos(t), -np.sin(t)], [np.sin(t), np.cos(t)]])
    return (pts - c) @ r.T + c + [500, 500]


@pytest.mark.parametrize("deg", range(0, 360, 5))
def test_order_points_liefert_vier_verschiedene_punkte(deg):
    ordered = tl.order_points(rotate(BASE, deg))
    assert len({tuple(p) for p in ordered}) == 4


@pytest.mark.parametrize("deg", range(0, 360, 5))
def test_order_points_ist_konvex_im_uhrzeigersinn(deg):
    ordered = tl.order_points(rotate(BASE, deg)).astype("float64")
    edges = np.roll(ordered, -1, axis=0) - ordered
    nxt = np.roll(edges, -1, axis=0)
    cross = edges[:, 0] * nxt[:, 1] - edges[:, 1] * nxt[:, 0]
    assert np.all(cross > 0)


def test_order_points_ungedrehtes_viereck():
    ordered = tl.order_points(np.array(TRAPEZ, dtype="float32"))
    assert ordered.tolist() == [[150, 100], [350, 100], [420, 300], [80, 300]]


def test_order_points_unabhaengig_von_der_eingabereihenfolge():
    # Der Editor liefert die Ecken in Z-Reihenfolge (oben links, oben rechts,
    # unten links, unten rechts), nicht im Umlaufsinn.
    z_order = [TRAPEZ[0], TRAPEZ[1], TRAPEZ[3], TRAPEZ[2]]
    expected = tl.order_points(np.array(TRAPEZ, dtype="float32"))
    assert np.array_equal(tl.order_points(np.array(z_order, dtype="float32")), expected)


@pytest.mark.parametrize("deg", (0, 20, 45, 90, 135))
def test_perspektivmatrix_ist_nicht_degeneriert(deg):
    matrix = tl.compute_perspective_matrix(rotate(BASE, deg), (780, 780))
    assert abs(np.linalg.det(matrix)) > 1e-9


def test_is_valid_quad_akzeptiert_rechteck_und_trapez():
    assert tl.is_valid_quad(BASE)
    assert tl.is_valid_quad(TRAPEZ)
    assert tl.is_valid_quad(rotate(BASE, 45))


def test_is_valid_quad_lehnt_unbrauchbare_vierecke_ab():
    assert not tl.is_valid_quad([[0, 0], [0, 0], [0, 0], [0, 0]])
    # drei Punkte auf einer Linie
    assert not tl.is_valid_quad([[0, 0], [100, 0], [200, 0], [100, 100]])
    # nicht konvex (Pfeilspitze)
    assert not tl.is_valid_quad([[0, 0], [200, 0], [100, 50], [100, 200]])
    # zu klein
    assert not tl.is_valid_quad([[0, 0], [5, 0], [5, 5], [0, 5]])
    # falsche Punktanzahl
    assert not tl.is_valid_quad([[0, 0], [100, 0], [100, 100]])


def test_crop_bounds_clamped_auf_null():
    assert tl.crop_bounds([np.array([[5, 5], [50, 60]])], 15) == (0, 0, 65, 75)


def test_clamp_zoom_center():
    assert tl.clamp_zoom_center((0, 100)) == (100 / 6, 100 - 100 / 6)
    assert tl.clamp_zoom_center((50, 50)) == (50, 50)


def gradient(height, width):
    y, x = np.mgrid[0:height, 0:width]
    return np.dstack([x, y, np.zeros_like(x)]).astype(np.uint16)


def test_crop_schneidet_den_ausschnitt_um_die_mitte_aus():
    img = gradient(600, 900)
    out = tl.crop(img, 3, (50, 50))
    assert out.shape == img.shape
    # Mitte des Ausschnitts = Bildmitte, Kanal 0 = x, Kanal 1 = y.
    assert abs(int(out[300, 450, 0]) - 450) <= 2 and abs(int(out[300, 450, 1]) - 300) <= 2


@pytest.mark.parametrize('center', [(0, 0), (100, 100), (0, 100), (100, 0), (16.6, 16.6)])
def test_crop_bleibt_am_rand_im_bild(center):
    # Auch eine Mitte am Rand ergibt den Ausschnitt an der Bildkante, nie ein
    # verschobenes oder leeres Stueck.
    img = gradient(781, 779)
    out = tl.crop(img, 3, center)
    assert out.shape == img.shape
    left = int(out[0, 0, 0]) if center[0] < 50 else int(out[0, -1, 0])
    assert left in ((0, 1) if center[0] < 50 else (777, 778))


def test_perspektivmatrix_bildet_die_ecken_auf_ein_nicht_quadratisches_rechteck_ab():
    import cv2
    dsize = (900, 600)
    matrix = tl.compute_perspective_matrix(TRAPEZ, dsize)
    corners = np.array([tl.order_points(TRAPEZ)], dtype=np.float32)
    mapped = cv2.perspectiveTransform(corners, matrix)[0]
    assert np.allclose(mapped, [[0, 0], [899, 0], [899, 599], [0, 599]], atol=1e-3)


def test_crop_bei_nicht_quadratischem_bild_und_mitte_ausserhalb_der_bildmitte():
    img = gradient(600, 900)
    out = tl.crop(img, 3, (25, 75))
    assert out.shape == img.shape
    # Mitte des Ausschnitts = (25 % von 900, 75 % von 600), Kanal 0 = x, 1 = y.
    assert abs(int(out[300, 450, 0]) - 225) <= 3
    assert abs(int(out[300, 450, 1]) - 450) <= 3


def test_crop_bounds_oberer_rand_wird_nicht_begrenzt():
    # x1/y1 begrenzt erst die Kamera gegen die echte Bildgroesse.
    assert tl.crop_bounds([np.array([[10, 20], [990, 790]])], 15) == (0, 5, 1005, 805)
