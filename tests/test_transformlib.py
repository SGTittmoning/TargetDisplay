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
