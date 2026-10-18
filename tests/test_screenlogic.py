"""Zoom und FPS-Anzeige ohne Oberflaeche."""
import cv2
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
    # Erste Stufe (3,65x auf Ganze Scheibe): die Spalten liegen enger beieinander.
    assert int(out[30, 60, 0]) - int(out[30, 30, 0]) == pytest.approx(30 / 3.65, abs=1)


@pytest.mark.parametrize('tap, direction', [((90, 50), (1, 0)), ((10, 50), (-1, 0)),
                                            ((50, 90), (0, 1)), ((50, 10), (0, -1)),
                                            ((50, 50), (0, 0))])
def test_weitere_tipps_am_rand_verschieben_um_ein_viertel_des_sichtbaren_bildes(view, tap, direction):
    view.tap(50, 50)
    # Sichtbar sind 100 / Faktor Prozent der Ansicht; ein Schritt ist ein Viertel davon.
    step = 25 / view.factor()
    view.tap(*tap)
    assert view.center == pytest.approx((50 + direction[0] * step, 50 + direction[1] * step))


def test_scrollweite_haengt_vom_zoom_ab_und_ist_immer_ein_viertel_des_sichtbaren():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE)
    view.tap(50, 50)
    first = view.factor()
    view.tap(95, 50)
    assert view.center[0] - 50 == pytest.approx(25 / first)
    view.zoom_in()
    before = view.center[0]
    view.tap(95, 50)
    # Stufe 2 zeigt weniger Bild: der Schritt ist kleiner, bleibt aber ein Viertel.
    assert view.center[0] - before == pytest.approx(25 / view.factor())
    assert 25 / view.factor() < 25 / first


def test_ausschnitt_bleibt_im_bild(view):
    view.tap(0, 0)
    assert view.center == tl.clamp_zoom_center((0, 0), view.factor())
    for _ in range(30):
        view.tap(1, 1)
    assert view.center == tl.clamp_zoom_center((0, 0), view.factor())


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


@pytest.mark.parametrize('level, detail, first, second', [
    ('full', 2.11, 3.65, 6.3),
    ('detail', 2.11, 4.205, 6.3),
])
def test_zoomstufen_liegen_auf_halbem_weg_und_am_maximum(level, detail, first, second):
    # Stufe 1: halber Weg von der gewaehlten Ansicht zum Maximum 6,3x, Stufe 2:
    # das Maximum; absolut bezogen auf "Ganze Scheibe".
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE, detail_factor=detail)
    view.set_level(level)
    view.zoom_in()
    assert view.absolute_factor() == pytest.approx(first, abs=0.01)
    view.zoom_in()
    assert view.absolute_factor() == pytest.approx(second)


def test_erste_stufe_in_innen_scheibe_ist_deutlich_groesser_als_die_ansicht():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE, detail_factor=2.11)
    view.set_level('detail')
    view.tap(50, 50)
    # Relativ zum Anzeigebild fast 2x, nicht nur 1,4x wie bei 3x absolut.
    assert view.factor() == pytest.approx(4.205 / 2.11, abs=0.01)
    assert view.factor() > 1.9


def test_ansicht_ueber_dem_maximum_vergroessert_nicht_weiter():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE, detail_factor=8.0)
    view.set_level('detail')
    view.zoom_in()
    view.zoom_in()
    assert view.factor() == 1.0


def test_maximum_ist_in_beiden_ansichten_absolut_gleich():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, SIZE, detail_factor=2.0)
    for level, factor in (('full', tl.VIDEO_ZOOM_MAX), ('detail', tl.VIDEO_ZOOM_MAX / 2)):
        # Innen Scheibe ist hier 2x: relativ zu ihrem Bild nur die Haelfte.
        view.set_level(level)
        view.zoom_in()
        view.zoom_in()
        assert view.step == 2 and view.factor() == pytest.approx(factor)
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
    for _ in range(20):
        view.tap(1, 1)
    first = view.factor()
    assert view.center == tl.clamp_zoom_center((0, 0), first)
    view.zoom_in()
    # Beim Maximum darf die Mitte naeher an den Rand als auf Stufe 1.
    assert view.center == tl.clamp_zoom_center((0, 0), first)
    for _ in range(20):
        view.tap(1, 1)
    assert view.center == tl.clamp_zoom_center((0, 0), tl.VIDEO_ZOOM_MAX)
    view.zoom_out()
    assert view.center == tl.clamp_zoom_center((0, 0), first)


def test_ansicht_wechsel_hebt_beide_stufen_auf(view):
    view.tap(40, 40)
    view.zoom_in()
    view.set_level('detail')
    assert (view.step, view.center) == (0, None)


def test_zoomfelder_treffer_nur_auf_plus_und_minus_und_nur_mit_tipp_zoom():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (600, 400))
    rects = view._control_rects()
    pct = lambda x, y: (x * 100 / 600, y * 100 / 400)  # noqa: E731
    centers = {name: pct((r[0] + r[2]) / 2, (r[1] + r[3]) / 2) for name, r in rects.items()}
    # Ohne Tipp-Zoom gibt es keine Felder.
    assert view.hit(*centers['in']) is None and view.hit(*centers['out']) is None
    view.tap(50, 50)
    for name in ('in', 'out'):
        assert view.hit(*centers[name]) == name
    x0, y0, x1, y1 = rects['out']
    assert view.hit(*pct(x0 - 2, y0 - 2)) is None
    assert view.hit(50, 50) is None


def test_zoomfelder_nur_mit_tipp_zoom_im_bild_und_zeigen_den_zustand():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (600, 400))
    blank = lambda: np.full((400, 600, 3), 100, np.uint8)  # noqa: E731
    rects = view._control_rects()
    for x0, y0, x1, y1 in rects.values():
        assert 0 <= x0 < x1 <= 600 and 0 <= y0 < y1 <= 400
    # Ohne Tipp-Zoom bleibt das Bild unveraendert.
    assert np.array_equal(view.render_with_controls(blank()), view.render(blank()))
    view.tap(50, 50)

    def brightness(name):
        x0, y0, x1, y1 = rects[name]
        return view.render_with_controls(blank())[y0:y1, x0:x1].max()
    # Stufe 1: beide Felder kraeftig; Maximum: "+" blass, "-" kraeftig.
    assert brightness('in') == brightness('out') > 200
    view.zoom_in()
    assert brightness('in') < brightness('out')
    # Beim Zuruecksetzen verschwinden die Felder wieder.
    view.zoom_out()
    view.zoom_out()
    assert np.array_equal(view.render_with_controls(blank()), view.render(blank()))


def triangle_image(view, base=120):
    plain = view.render(np.full((60, 90, 3), base, np.uint8))
    shown = view.render_with_controls(np.full((60, 90, 3), base, np.uint8))
    return plain, shown


def triangle_pixels(view, direction):
    """Zahl der durch das Dreieck veraenderten Bildpunkte."""
    plain, shown = triangle_image(view)
    mask = np.zeros(plain.shape[:2], np.uint8)
    cv2.fillPoly(mask, [view._triangle(direction)], 255)
    return int(((shown != plain).any(axis=2) & (mask > 0)).sum())


def test_ohne_tipp_zoom_keine_dreiecke():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (780, 780))
    assert view.pan_directions() == []
    assert triangle_pixels(view, 'left') == 0


def test_in_der_mitte_vier_dreiecke_sichtbar():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (780, 780))
    view.tap(50, 50)
    assert sorted(view.pan_directions()) == ['down', 'left', 'right', 'up']
    for direction in ('up', 'down', 'left', 'right'):
        assert triangle_pixels(view, direction) > 500, direction


@pytest.mark.parametrize('center, hidden', [((0, 50), 'left'), ((100, 50), 'right'),
                                            ((50, 0), 'up'), ((50, 100), 'down')])
def test_am_rand_verschwindet_das_dreieck_dieser_richtung(center, hidden):
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (780, 780))
    view.tap(50, 50)
    view.center = tl.clamp_zoom_center(center, view.factor())
    assert hidden not in view.pan_directions()
    assert len(view.pan_directions()) == 3
    assert triangle_pixels(view, hidden) == 0


def test_in_der_ecke_zwei_dreiecke():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (780, 780))
    view.tap(0, 0)
    assert sorted(view.pan_directions()) == ['down', 'right']


def test_dreiecke_haben_einen_schwarzen_rand_und_helle_fuellung():
    eye = np.eye(3, dtype=np.float64)
    view = sl.Viewport(eye, eye, (780, 780))
    view.tap(50, 50)
    plain, shown = triangle_image(view, base=120)
    shown = view.render_with_controls(np.full((780, 780, 3), 120, np.uint8))
    tri = view._triangle('up')
    inside = shown[int(tri[:, 1].mean()), int(tri[:, 0].mean())]
    assert inside.min() > 150            # halbtransparent weiss auf mittlerem Grau
    top = tri[2]                         # Spitze: Linie des schwarzen Rands
    assert shown[top[1] + 1, top[0]].max() < 60


def test_dreiecke_skalieren_mit_der_bildbreite():
    eye = np.eye(3, dtype=np.float64)
    wide, narrow = sl.Viewport(eye, eye, (780, 780)), sl.Viewport(eye, eye, (390, 390))
    w1 = np.ptp(wide._triangle('up')[:, 0])
    w2 = np.ptp(narrow._triangle('up')[:, 0])
    assert w1 == sl.TRIANGLE_W and abs(w2 * 2 - w1) <= 2
