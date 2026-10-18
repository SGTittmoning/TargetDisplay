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
    assert view.key == ('full', 0, None)
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
    assert view.key == ('detail', 0, None)


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


def span_of(image):
    # x-Verlauf: Breite des sichtbaren Bereichs im Anzeigebild.
    return int(image[30, -1, 0]) - int(image[30, 0, 0])


def test_normale_stufe_ist_in_beiden_ansichten_absolut_gleich():
    # "Innen Scheibe" ist hier schon doppelt vergroessert: die normale Stufe
    # (absolut 3x) zeigt dort ein Drittel mal zwei, also 2/3 der Breite
    # des Anzeigebilds - dieselbe Schaerfe wie 3x in "Ganze Scheibe".
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE, detail_factor=2.0)
    view.tap(50, 50)
    assert abs(span_of(view.render(gradient())) - SIZE[0] / 3) <= 2
    view.set_level('detail')
    view.tap(50, 50)
    assert view.factor() == pytest.approx(1.5)
    assert abs(span_of(view.render(gradient())) - SIZE[0] / 1.5) <= 2


def test_maximum_ist_in_beiden_ansichten_absolut_gleich():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE, detail_factor=2.0)
    for level, factor in (('full', 6), ('detail', 3)):
        view.set_level(level)
        view.zoom_in()
        view.zoom_in()
        assert view.step == 2 and view.factor() == factor
        assert abs(span_of(view.render(gradient())) - SIZE[0] / factor) <= 2


def test_zoom_in_und_out_gehen_stufenweise():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE)
    view.zoom_in()
    assert (view.step, view.center) == (1, (50, 50))
    view.zoom_in()
    assert view.step == 2
    view.zoom_in()
    assert view.step == 2
    view.zoom_out()
    assert view.step == 1
    view.zoom_out()
    assert (view.step, view.center) == (0, None)
    view.zoom_out()
    assert view.step == 0


def test_stufenwechsel_begrenzt_die_mitte_neu():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE)
    view.zoom_in()
    view.zoom_in()
    for _ in range(20):
        view.tap(1, 1)
    # Sechsfach darf die Mitte naeher an den Rand als dreifach.
    assert view.center == tl.clamp_zoom_center((0, 0), 6)
    view.zoom_out()
    assert view.center == tl.clamp_zoom_center((0, 0), 3)
    view.zoom_in()
    assert view.center == tl.clamp_zoom_center((0, 0), 3)


def test_ansicht_wechsel_hebt_beide_stufen_auf(view):
    view.tap(40, 40)
    view.zoom_in()
    view.set_level('detail')
    assert (view.step, view.center) == (0, None)


def test_zoomfelder_treffer_nur_auf_plus_und_minus():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (600, 400))
    rects = view._control_rects()
    pct = lambda x, y: (x * 100 / 600, y * 100 / 400)  # noqa: E731
    x0, y0, x1, y1 = rects['in']
    assert view.hit(*pct((x0 + x1) / 2, (y0 + y1) / 2)) == 'in'
    x0, y0, x1, y1 = rects['out']
    assert view.hit(*pct((x0 + x1) / 2, (y0 + y1) / 2)) == 'out'
    x0, y0, x1, y1 = rects['label']
    assert view.hit(*pct((x0 + x1) / 2, (y0 + y1) / 2)) is None
    assert view.hit(50, 50) is None
    assert view.hit(*pct(x0 - 2, y0 - 2)) is None


def test_zoomfelder_liegen_im_bild_und_zeigen_die_stufe():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (600, 400))
    plain = view.render(np.zeros((400, 600, 3), np.uint8))
    shown = view.render_with_controls(np.zeros((400, 600, 3), np.uint8))
    for x0, y0, x1, y1 in view._control_rects().values():
        assert 0 <= x0 < x1 <= 600 and 0 <= y0 < y1 <= 400
    # Nur die Felder unterscheiden sich vom reinen Bild.
    diff = np.argwhere((shown != plain).any(axis=2))
    xs0 = min(r[0] for r in view._control_rects().values())
    assert diff[:, 1].min() >= xs0
    # Mit Tipp-Zoom steht die Stufe im Feld in der Mitte (mehr Pixel gezeichnet).
    view.tap(50, 50)
    with_text = view.render_with_controls(np.zeros((400, 600, 3), np.uint8))
    x0, y0, x1, y1 = view._control_rects()['label']
    assert (with_text[y0:y1, x0:x1] > 200).any()
    assert not (shown[y0:y1, x0:x1] > 200).any()
