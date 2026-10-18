"""Zoom und FPS-Anzeige ohne Oberflaeche."""
import numpy as np
import pytest

import screenlogic as sl
import transformlib as tl

SIZE = (90, 60)


def gradient():
    # Kanal 0 = x, Kanal 1 = y; die Einheitsmatrix laesst das Bild unveraendert.
    y, x = np.mgrid[0:SIZE[1], 0:SIZE[0]]
    return np.dstack([x, y, np.zeros_like(x)]).astype(np.uint8)


@pytest.fixture
def view():
    eye = np.eye(3, dtype=np.float64)
    shift = np.array([[1, 0, -10], [0, 1, -5], [0, 0, 1]], dtype=np.float64)
    return sl.Viewport(eye, shift, SIZE)


def test_start_ganze_scheibe_ohne_tipp_zoom(view):
    assert view.key == ('full', None)
    assert np.array_equal(view.render(gradient()), gradient())


def test_innen_scheibe_nutzt_die_eigene_entzerrung(view):
    view.set_level('detail')
    out = view.render(gradient())
    assert out[0, 0, 0] == 10 and out[0, 0, 1] == 5


def test_erster_tipp_vergroessert_um_die_tippstelle(view):
    view.tap(50, 50)
    assert view.center == (50, 50)
    out = view.render(gradient())
    # Dreifach vergroessert: die Spalten liegen enger beieinander.
    assert int(out[30, 60, 0]) - int(out[30, 30, 0]) == pytest.approx(10, abs=1)


@pytest.mark.parametrize('tap, moved', [((90, 50), (55, 50)), ((10, 50), (45, 50)),
                                        ((50, 90), (50, 55)), ((50, 10), (50, 45)),
                                        ((50, 50), (50, 50))])
def test_weitere_tipps_am_rand_verschieben(view, tap, moved):
    view.tap(50, 50)
    view.tap(*tap)
    assert view.center == moved


def test_ausschnitt_bleibt_im_bild(view):
    view.tap(0, 0)
    assert view.center == tl.clamp_zoom_center((0, 0))
    for _ in range(30):
        view.tap(1, 1)
    assert view.center == tl.clamp_zoom_center((0, 0))


def test_zoomstufe_hebt_tipp_zoom_auf(view):
    view.tap(40, 40)
    view.set_level('detail')
    assert view.key == ('detail', None)


def test_fps_zaehler():
    fps = sl.FpsCounter()
    assert fps.tick(100.0) is None          # noch keine Zeitspanne
    for i in range(1, 7):
        text = fps.tick(100.0 + i / 6)
    assert text == 'FPS: 7.0 - Frame 7'


def test_fps_nur_ueber_das_letzte_zeitfenster():
    fps = sl.FpsCounter()
    for i in range(10):
        fps.tick(float(i))
    fps.tick(10.0 + sl.FPS_WINDOW_SECONDS)
    # Die alten Zeitstempel sind herausgefallen (2 Bilder in 1 s), der
    # Bildzaehler laeuft weiter.
    assert fps.tick(11.0 + sl.FPS_WINDOW_SECONDS) == 'FPS: 2.0 - Frame 12'


def test_tipp_zoom_ist_in_beiden_stufen_dreifach(view):
    # Der Tipp-Zoom wirkt auf das entzerrte Anzeigebild: in beiden Stufen
    # zeigt er ein Drittel der Breite, also dreifache Vergroesserung.
    for level in ('full', 'detail'):
        view.set_level(level)
        view.tap(50, 50)
        image = view.render(gradient())
        # x-Verlauf: der sichtbare Bereich umfasst ein Drittel des Anzeigebilds.
        span = int(image[30, -1, 0]) - int(image[30, 0, 0])
        assert abs(span - SIZE[0] / 3) <= 2, level
