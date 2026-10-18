"""Hauptbildschirm unter Xvfb: Sperren waehrend Timer, Blinken, Replay und
"Video aus", Zoom per Tippen, Timer bis zum selbststaendigen Ende,
Exit-Codes und Replay-Ablauf.

Zustaende werden mit Messpunkten ('-PROBE-name-', siehe app_harness.run_app)
genau im jeweiligen Moment festgehalten.
"""
import os
import re
import time
from datetime import datetime

import numpy as np
import pytest
from app_harness import (
    ROOT,
    CountingCamera,
    FakeCamera,
    is_replay_frame,
    is_timer_frame,
    needs_display,
    run_app,
)

pytestmark = needs_display

ZOOM = ('-FULL_VIDEO-', '-DETAIL_VIDEO-')
BLINK = ('-BLINK_START-', '-BLINK_REF-', '-BLINK_STOP-')
TIMERS = ('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-')


class GradientCamera(FakeCamera):
    """Bild mit Verlauf, damit Zoom und Ausschnitt sichtbar werden."""

    def __init__(self, url, **kwargs):
        super().__init__(url)
        self.stale = False
        self.blank = False
        y, x = np.mgrid[0:700, 0:700]
        self.image = np.dstack([x % 256, y % 256, (x + y) % 256]).astype(np.uint8)
        GradientCamera.last = self

    def get_frame(self, full=False):
        self.last_frame_time = time.monotonic()
        return None if self.blank else self.image.copy()

    def is_stale(self, timeout, startup_timeout=None):
        return self.stale


@pytest.fixture
def gradient(app, monkeypatch):
    monkeypatch.setattr(app, 'Camera', GradientCamera)
    return app


@pytest.fixture
def short_timers(monkeypatch):
    import timerlib
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    monkeypatch.setattr(timerlib, 'STOP_TIME', 1)
    monkeypatch.setattr(timerlib, 'SINGLE_SHOW', {timerlib.TIMER_20: 2, timerlib.TIMER_10: 1})
    monkeypatch.setattr(timerlib, 'SERIES_SHOW', 1)
    monkeypatch.setattr(timerlib, 'SERIES_HIDE', 1)
    monkeypatch.setattr(timerlib, 'SERIES_LOOPS', 2)
    return timerlib


def only(state, *keys):
    """Genau diese Buttons sind bedienbar."""
    return {k for k, v in state['clickable'].items() if v} == set(keys)


def color_at_bottom(frame):
    # Unten Mitte, innerhalb des Replay-Rahmens und frei von jedem Text
    # (Zahl oben links, Countdown und Trenntext in der Mitte).
    return tuple(int(v) for v in frame[575, 300])


def is_green(frame):
    return color_at_bottom(frame) == (0, 255, 0)


def has_number(frame):
    # Durchgangszahl oben links (schwarz auf gruen/rot), siehe main.py.
    return (frame[30:140, 15:140] == 0).all(axis=2).any()


# ------------------------------------------------------------ Sperren (T3)

def test_ausgangszustand(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-start-')], monkeypatch, probes=probes)
    assert only(probes['start'], *ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-')


def test_sperren_waehrend_des_timers(gradient, monkeypatch, short_timers):
    probes = {}
    run_app(gradient, [(1.0, '-TIMER_10-'), (0.5, '-PROBE-timer-')], monkeypatch, probes=probes)
    assert only(probes['timer'], '-TIMER_STOP-')


def test_sperren_waehrend_des_blinkens(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-BLINK_START-'), (0.3, '-PROBE-blinken-'),
                       (0.3, '-BLINK_STOP-'), (0.3, '-PROBE-danach-')], monkeypatch, probes=probes)
    # Zoom und Timer bleiben bedienbar, Video aus nicht.
    assert only(probes['blinken'], *ZOOM, '-BLINK_REF-', '-BLINK_STOP-', *TIMERS)
    assert only(probes['danach'], *ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-')


def gray(frame):
    return int(frame[300, 300, 0])


def test_blinken_referenz_nimmt_ein_neues_bezugsbild_auf(app, monkeypatch):
    # Die Kamera liefert jedes Bild in einem anderen, steigenden Grauwert.
    # Ein Livebild erscheint nur einmal, die Referenz beim Blinken immer
    # wieder: der haeufigste Grauwert ist die Referenz. Nach "Referenz" muss
    # sie heller (neuer) sein als die von "Start".
    monkeypatch.setattr(app, 'Camera', CountingCamera)
    probes = {}
    draws = run_app(app, [(1.0, '-BLINK_START-'), (2.5, '-PROBE-vorher-'), (0.0, '-BLINK_REF-'),
                          (2.5, '-PROBE-nachher-')], monkeypatch, probes=probes)
    split = probes['vorher']['draws']
    before = [gray(d) for d in draws[:split]]
    after = [gray(d) for d in draws[split:probes['nachher']['draws']]]
    ref_before = max(set(before), key=before.count)
    ref_after = max(set(after), key=after.count)
    assert before.count(ref_before) > 1 and after.count(ref_after) > 1
    assert ref_after > ref_before


def test_sperren_bei_video_aus(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-TOGGLEVIDEO-'), (0.3, '-PROBE-aus-'),
                       (0.3, '-TOGGLEVIDEO-'), (0.3, '-PROBE-ein-')], monkeypatch, probes=probes)
    assert only(probes['aus'], '-TOGGLEVIDEO-')
    assert only(probes['ein'], *ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-')


def test_sperren_waehrend_des_replays(gradient, monkeypatch, short_timers):
    probes = {}
    # Timer: 1 s Vorbereitung, 1 s Gruen, 1 s Stopp.
    run_app(gradient, [(1.0, '-TIMER_10-'), (4.5, '-PROBE-aufnahme-'),
                       (0.2, '-REPLAY-'), (0.3, '-PROBE-replay-'),
                       (0.2, '-REPLAY_END-'), (0.3, '-PROBE-ende-')], monkeypatch, probes=probes)
    assert only(probes['aufnahme'], *ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-')
    # Im Replay: Zoom bedienbar, Replay Ende, sonst nichts.
    assert only(probes['replay'], *ZOOM, '-REPLAY-')
    assert probes['replay']['replay_text'] == 'Replay Ende'
    assert only(probes['ende'], *ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-')


def test_blinken_laeuft_nach_replay_weiter(gradient, monkeypatch, short_timers):
    probes = {}
    run_app(gradient, [(1.0, '-BLINK_START-'), (0.3, '-TIMER_10-'), (3.5, '-REPLAY-'),
                       (0.3, '-PROBE-replay-'), (0.2, '-REPLAY_END-'), (0.3, '-PROBE-ende-')],
            monkeypatch, probes=probes)
    assert only(probes['replay'], *ZOOM, '-REPLAY-')
    assert only(probes['ende'], *ZOOM, '-BLINK_REF-', '-BLINK_STOP-', *TIMERS, '-REPLAY-')


# ------------------------------------------------------------- Zoom (T4)

def tap(x, y):
    return ('-VIDEO-', {'-VIDEO-': (x, y)})


def test_tippen_zoomt_verschiebt_und_reset_stellt_zurueck(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-start-'),
                       (0.1, tap(50, 50)), (0.4, '-PROBE-zoom-'),
                       (0.1, tap(90, 50)), (0.4, '-PROBE-rechts-'),
                       (0.1, '-RESETZOOM-'), (0.4, '-PROBE-reset-')], monkeypatch, probes=probes)
    start, zoom, rechts, reset = (probes[k] for k in ('start', 'zoom', 'rechts', 'reset'))
    assert not start['clickable']['-RESETZOOM-']
    assert zoom['clickable']['-RESETZOOM-']
    assert not np.array_equal(zoom['frame'], start['frame'])
    # Tippen nahe am rechten Rand verschiebt den Ausschnitt nach rechts:
    # der rote Kanal (x-Verlauf) wird groesser.
    assert int(rechts['frame'][300, 300, 2]) > int(zoom['frame'][300, 300, 2])
    assert np.array_equal(reset['frame'], start['frame'])
    assert not reset['clickable']['-RESETZOOM-']


def test_tippen_oben_und_unten_verschiebt_senkrecht(gradient, monkeypatch):
    # Kanal 1 = y-Verlauf: nach unten verschoben wird er groesser.
    probes = {}
    run_app(gradient, [(1.0, tap(50, 50)), (0.4, '-PROBE-zoom-'),
                       (0.1, tap(50, 90)), (0.4, '-PROBE-unten-'),
                       (0.1, tap(50, 10)), (0.1, tap(50, 10)), (0.4, '-PROBE-oben-')],
            monkeypatch, probes=probes)
    green = {k: int(probes[k]['frame'][300, 300, 1]) for k in ('zoom', 'unten', 'oben')}
    assert green['unten'] > green['zoom'] > green['oben']
    # Waagerecht bleibt alles gleich (Kanal 0 = x-Verlauf).
    assert len({int(probes[k]['frame'][300, 300, 0]) for k in ('zoom', 'unten', 'oben')}) == 1


def test_innen_scheibe_hebt_den_tipp_zoom_auf(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-DETAIL_VIDEO-'), (0.4, '-PROBE-innen-'),
                       (0.1, tap(20, 20)), (0.4, '-PROBE-zoom-'),
                       (0.1, '-DETAIL_VIDEO-'), (0.4, '-PROBE-wieder-')], monkeypatch, probes=probes)
    assert not np.array_equal(probes['zoom']['frame'], probes['innen']['frame'])
    assert np.array_equal(probes['wieder']['frame'], probes['innen']['frame'])


def test_reset_setzt_auch_innen_scheibe_zurueck(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-start-'), (0.1, '-DETAIL_VIDEO-'), (0.4, '-PROBE-innen-'),
                       (0.1, '-RESETZOOM-'), (0.4, '-PROBE-reset-')], monkeypatch, probes=probes)
    assert probes['innen']['clickable']['-RESETZOOM-']
    assert not np.array_equal(probes['innen']['frame'], probes['start']['frame'])
    assert np.array_equal(probes['reset']['frame'], probes['start']['frame'])
    assert not probes['reset']['clickable']['-RESETZOOM-']


def test_ganze_scheibe_hebt_den_ausschnitt_auf(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-start-'), (0.1, tap(50, 50)), (0.3, '-FULL_VIDEO-'),
                       (0.4, '-PROBE-ganz-')], monkeypatch, probes=probes)
    assert np.array_equal(probes['ganz']['frame'], probes['start']['frame'])
    assert not probes['ganz']['clickable']['-RESETZOOM-']


def test_tippen_im_timer_und_bei_video_aus_zoomt_nicht(gradient, monkeypatch, short_timers):
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-start-'),
                       (0.1, '-TIMER_10-'), (0.3, tap(50, 50)), (4.0, '-PROBE-nach-timer-'),
                       (0.1, '-TOGGLEVIDEO-'), (0.2, tap(50, 50)), (0.2, '-TOGGLEVIDEO-'),
                       (0.4, '-PROBE-nach-video-aus-')], monkeypatch, probes=probes)
    for key in ('nach-timer', 'nach-video-aus'):
        assert np.array_equal(probes[key]['frame'], probes['start']['frame'])
        assert not probes[key]['clickable']['-RESETZOOM-']


def test_ausschnitt_wandert_nicht_beliebig_weit(gradient, monkeypatch):
    probes = {}
    steps = [(1.0, tap(50, 50))] + [(0.05, tap(99, 50))] * 30 + [(0.4, '-PROBE-rand-')]
    steps += [(0.05, tap(1, 50))] + [(0.4, '-PROBE-zurueck-')]
    run_app(gradient, steps, monkeypatch, probes=probes)
    # Ein einziger Tipp zurueck bewegt den Ausschnitt sofort wieder.
    assert not np.array_equal(probes['rand']['frame'], probes['zurueck']['frame'])


# ------------------------------------------------------- Timer-Ende (T5)

def test_timer_endet_von_selbst(gradient, monkeypatch, short_timers):
    probes = {}
    draws = run_app(gradient, [(1.0, '-TIMER_10-'), (4.0, '-PROBE-danach-')], monkeypatch, probes=probes)
    assert any(is_green(d) for d in draws)
    after = probes['danach']
    assert not is_timer_frame(after['frame'])
    assert only(after, *ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-')
    assert after['fps'].startswith('FPS')


def test_serie_zeigt_durchgangszahl(gradient, monkeypatch, short_timers):
    draws = run_app(gradient, [(1.0, '-TIMER_5_3_7-'), (5.5, '-FULL_VIDEO-')], monkeypatch)
    green = [d for d in draws if is_green(d)]
    assert green and all(has_number(d) for d in green)


def test_einzeltimer_ohne_durchgangszahl(gradient, monkeypatch, short_timers):
    draws = run_app(gradient, [(1.0, '-TIMER_20-'), (2.5, '-FULL_VIDEO-')], monkeypatch)
    green = [d for d in draws if is_green(d)]
    assert green and not any(has_number(d) for d in green)


def drawn_texts(monkeypatch):
    import overlays
    texts = []
    real = overlays.cv2.putText
    monkeypatch.setattr(overlays.cv2, 'putText',
                        lambda img, text, *a, **k: (texts.append(text), real(img, text, *a, **k))[1])
    return texts


def test_serie_zeigt_countdown_und_durchgang(gradient, monkeypatch, short_timers):
    texts = drawn_texts(monkeypatch)
    run_app(gradient, [(1.0, '-TIMER_5_3_7-'), (5.5, '-FULL_VIDEO-')], monkeypatch)
    # Vorbereitung 1 s (Countdown), zwei Durchgaenge je 1 s Gruen und
    # dazwischen 1 s Pause (jeweils Nummer und Countdown), 1 s Stopp ohne Text.
    assert texts == ['1', '1', '1', '1', '1', '2', '1']


def test_replay_zeigt_titel_trenntext_und_zaehler(gradient, monkeypatch, short_timers):
    texts = drawn_texts(monkeypatch)
    # Die Serie dauert 5 s, Replay mit Abstand danach.
    run_app(gradient, [(1.0, '-TIMER_5_3_7-'), (7.0, '-REPLAY-'), (5.0, '-FULL_VIDEO-')], monkeypatch)
    replay = texts[texts.index('5 x 3 Sek.'):]
    assert 'Durchgang 2/2' in replay
    counters = [t for t in replay if t.startswith('Durchgang ') and t.endswith(' s')]
    assert re.fullmatch(r'Durchgang 1/2  0,\d s', counters[0])
    assert re.fullmatch(r'Durchgang 2/2  \d,\d s', counters[-1])
    # Der Zaehler beginnt fuer Durchgang 2 von vorn.
    first_2 = next(t for t in counters if t.startswith('Durchgang 2/2'))
    assert first_2.startswith('Durchgang 2/2  0,')


# ---------------------------------------------------------- Exit-Codes (T7)

@pytest.fixture
def fast_stale(gradient, monkeypatch):
    monkeypatch.setattr(gradient, 'STREAM_STALE_TIMEOUT_SEC', 0.3)
    monkeypatch.setattr(gradient, 'STREAM_STARTUP_TIMEOUT_SEC', 0.3)
    return gradient


def stale(value):
    return lambda: setattr(GradientCamera.last, 'stale', value)


def test_stream_ausfall_beendet_mit_exit_2(fast_stale, monkeypatch):
    with pytest.raises(SystemExit) as exit_info:
        run_app(fast_stale, [(1.0, stale(True)), (3.0, '-FULL_VIDEO-')], monkeypatch)
    assert exit_info.value.code == 2


def test_kein_exit_waehrend_timer_und_video_aus(fast_stale, monkeypatch):
    # Beides braucht die Kamera nicht; ein Ausfall darf es nicht unterbrechen.
    probes = {}
    run_app(fast_stale, [(1.0, '-TIMER_10-'), (0.1, stale(True)), (1.5, '-PROBE-timer-'),
                         (0.1, '-TIMER_STOP-'), (0.1, '-TOGGLEVIDEO-'), (1.0, '-PROBE-aus-')],
            monkeypatch, probes=probes)
    assert set(probes) == {'timer', 'aus'}


def test_kein_exit_waehrend_replay(fast_stale, monkeypatch, short_timers):
    probes = {}
    run_app(fast_stale, [(1.0, '-TIMER_10-'), (3.5, '-REPLAY-'), (0.1, stale(True)),
                         (0.8, '-PROBE-replay-'), (0.1, '-TOGGLEVIDEO-')], monkeypatch, probes=probes)
    assert probes['replay']['replay_text'] == 'Replay Ende'


def test_gespeicherte_einstellungen_beenden_mit_exit_0(gradient, monkeypatch):
    monkeypatch.setattr(gradient, 'check_pin', lambda pin: True)
    monkeypatch.setattr(gradient, 'run_settings_flow', lambda *a: True)
    with pytest.raises(SystemExit) as exit_info:
        run_app(gradient, [(1.0, '-SETTINGS-'), (2.0, '-FULL_VIDEO-')], monkeypatch)
    assert exit_info.value.code == 0


# -------------------------------------------------------------- Replay (T10)

def is_red(frame):
    return color_at_bottom(frame) == (0, 0, 255)


def test_replay_serie_mit_trennbild(gradient, monkeypatch, short_timers):
    draws = run_app(gradient, [(1.0, '-TIMER_5_3_7-'), (6.0, '-REPLAY-'), (5.0, '-FULL_VIDEO-')], monkeypatch)
    replay = [d for d in draws if is_replay_frame(d)]
    kinds = []
    for d in replay:
        kind = 'rot' if is_red(d) else 'bild'
        if not kinds or kinds[-1] != kind:
            kinds.append(kind)
    # Startbild, Durchgang 1, Trennbild, Durchgang 2, Schlussbild
    assert kinds == ['rot', 'bild', 'rot', 'bild', 'rot']
    assert not is_replay_frame(draws[-1])


def test_replay_kein_bild_bei_ausgefallener_kamera(gradient, monkeypatch, short_timers):
    # Serie mit zwei Durchgaengen; im ersten liefert die Kamera kein Bild.
    def blank(value):
        return lambda: setattr(GradientCamera.last, 'blank', value)
    # Aufgenommen wird 1 s verzoegert (Latenz): Durchgang 1 von 2 bis 3 s,
    # Durchgang 2 von 4 bis 5 s nach dem Start. Die Kamera kommt dazwischen
    # zurueck, das Replay startet nach dem Ende der Serie.
    draws = run_app(gradient, [(1.0, blank(True)), (0.1, '-TIMER_5_3_7-'), (3.5, blank(False)),
                               (3.0, '-REPLAY-'), (5.5, '-FULL_VIDEO-')], monkeypatch)
    replay = [color_at_bottom(d) for d in draws if is_replay_frame(d) and not is_red(d)]
    # "kein Bild" ist schwarz mit grauer Schrift, danach das Kamerabild.
    assert (0, 0, 0) in replay
    assert any(c != (0, 0, 0) for c in replay)


def test_naechster_timer_ersetzt_die_aufnahme(gradient, monkeypatch, short_timers):
    probes = {}
    run_app(gradient, [(1.0, '-TIMER_10-'), (3.5, '-PROBE-erste-'), (0.1, '-TIMER_10-'),
                       (0.3, '-TIMER_STOP-'), (0.3, '-PROBE-zweite-')], monkeypatch, probes=probes)
    assert probes['erste']['clickable']['-REPLAY-']
    # Der zweite Timer wurde vor der Gruenphase gestoppt: die neue, leere
    # Aufnahme hat die alte ersetzt.
    assert not probes['zweite']['clickable']['-REPLAY-']


# ------------------------------------------------------------ Video aus (T11)

def test_video_aus_zeigt_logo(gradient, monkeypatch):
    os.symlink(os.path.join(ROOT, 'ressources'), 'ressources')
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-ein-'), (0.1, '-TOGGLEVIDEO-'), (0.4, '-PROBE-aus-')],
            monkeypatch, probes=probes)
    aus = probes['aus']
    assert probes['ein']['fps'].startswith('FPS')
    assert aus['fps'] == ''
    # Schwarzer Grund, in der Mitte das Logo
    assert (aus['frame'][5, 5] == 0).all()
    assert aus['frame'][250:350, 250:350].max() > 100


def test_video_aus_wechselt_das_symbol(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-ein-'), (0.1, '-TOGGLEVIDEO-'), (0.3, '-PROBE-aus-'),
                       (0.1, '-TOGGLEVIDEO-'), (0.3, '-PROBE-wieder-')], monkeypatch, probes=probes)
    assert probes['aus']['video_icon'] != probes['ein']['video_icon']
    assert probes['wieder']['video_icon'] == probes['ein']['video_icon']


# ------------------------------------------------- Kopf- und Fusszeile (T12)

def test_kopf_und_fusszeile(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.5, '-PROBE-a-'), (1.2, '-PROBE-b-')], monkeypatch, probes=probes)
    a, b = probes['a'], probes['b']
    assert a['stand'] == 'Teststand'
    assert a['date'] == datetime.now().strftime('%d.%m.%Y')
    assert re.fullmatch(r'\d{2}:\d{2}:\d{2}', a['time'])
    # Die Uhr laeuft weiter.
    assert b['time'] != a['time']
    match = re.fullmatch(r'FPS: (\d+\.\d) - Frame (\d+)', b['fps'])
    assert match and float(match.group(1)) > 0 and int(match.group(2)) > 1
