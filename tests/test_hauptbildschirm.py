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
# Einstellungen: nur im Livebild und bei Video aus bedienbar.
SETTINGS = '-SETTINGS-'


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


# ------------------------------------------------------------ Sperren (T3)

def test_ausgangszustand(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-PROBE-start-')], monkeypatch, probes=probes)
    assert only(probes['start'], *ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-', SETTINGS)


def test_sperren_waehrend_des_timers(gradient, monkeypatch, short_timers):
    probes = {}
    run_app(gradient, [(1.0, '-TIMER_10-'), (0.5, '-PROBE-timer-')], monkeypatch, probes=probes)
    assert only(probes['timer'], '-TIMER_STOP-')


def test_sperren_waehrend_des_blinkens(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-BLINK_START-'), (0.3, '-PROBE-blinken-'),
                       (0.3, '-BLINK_STOP-'), (0.3, '-PROBE-danach-')], monkeypatch, probes=probes)
    # Zoom und Timer bleiben bedienbar, Video aus nicht.
    assert only(probes['blinken'], *ZOOM, '-BLINK_REF-', '-BLINK_STOP-', *TIMERS, SETTINGS)
    assert only(probes['danach'], *ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-', SETTINGS)


def test_sperren_bei_video_aus(gradient, monkeypatch):
    probes = {}
    run_app(gradient, [(1.0, '-TOGGLEVIDEO-'), (0.3, '-PROBE-aus-'),
                       (0.3, '-TOGGLEVIDEO-'), (0.3, '-PROBE-ein-')], monkeypatch, probes=probes)
    assert only(probes['aus'], '-TOGGLEVIDEO-', SETTINGS)
    assert only(probes['ein'], *ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-', SETTINGS)


def test_sperren_waehrend_des_replays(gradient, monkeypatch, short_timers):
    probes = {}
    # Timer: 1 s Vorbereitung, 1 s Gruen, 1 s Stopp.
    run_app(gradient, [(1.0, '-TIMER_10-'), (4.5, '-PROBE-aufnahme-'),
                       (0.2, '-REPLAY-'), (0.3, '-PROBE-replay-'),
                       (0.2, '-REPLAY_END-'), (0.3, '-PROBE-ende-')], monkeypatch, probes=probes)
    assert only(probes['aufnahme'], *ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-', SETTINGS)
    # Im Replay: Zoom bedienbar, Replay Ende, sonst nichts.
    assert only(probes['replay'], *ZOOM, '-REPLAY-')
    assert probes['replay']['replay_text'] == 'Replay Ende'
    assert only(probes['ende'], *ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-', SETTINGS)


def test_einstellungen_im_timer_und_replay_ohne_wirkung(gradient, monkeypatch, short_timers):
    # Das gesperrte Zahnrad sendet nichts; kommt das Ereignis trotzdem an,
    # oeffnet sich das PIN-Feld nicht ueber Timer oder Replay.
    asked = []
    monkeypatch.setattr(gradient, 'check_pin', lambda pin: asked.append(pin) or False)
    probes = {}
    draws = run_app(gradient, [(1.0, '-TIMER_10-'), (0.3, '-SETTINGS-'), (0.5, '-PROBE-timer-'),
                               (3.0, '-REPLAY-'), (0.3, '-SETTINGS-'), (0.5, '-PROBE-replay-'),
                               (3.0, '-SETTINGS-')], monkeypatch, probes=probes)
    # Erst das letzte Ereignis (wieder im Livebild) fragt nach der PIN.
    assert len(asked) == 1
    # Der Timer lief sichtbar weiter: nach dem Tipp kamen weitere Timerbilder.
    assert probes['replay']['draws'] > probes['timer']['draws']
    assert any(is_replay_frame(d) for d in draws)


def test_blinken_laeuft_nach_replay_weiter(gradient, monkeypatch, short_timers):
    probes = {}
    run_app(gradient, [(1.0, '-BLINK_START-'), (0.3, '-TIMER_10-'), (3.5, '-REPLAY-'),
                       (0.3, '-PROBE-replay-'), (0.2, '-REPLAY_END-'), (0.3, '-PROBE-ende-')],
            monkeypatch, probes=probes)
    assert only(probes['replay'], *ZOOM, '-REPLAY-')
    assert only(probes['ende'], *ZOOM, '-BLINK_REF-', '-BLINK_STOP-', *TIMERS, '-REPLAY-', SETTINGS)


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
    assert only(after, *ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-', SETTINGS)
    assert after['fps'].startswith('FPS')


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
    before = datetime.now().strftime('%d.%m.%Y')
    run_app(gradient, [(1.5, '-PROBE-a-'), (1.2, '-PROBE-b-')], monkeypatch, probes=probes)
    a, b = probes['a'], probes['b']
    assert a['stand'] == 'Teststand'
    # Datum von vor und nach dem Lauf: ein Test um Mitternacht darf nicht
    # scheitern (der Datumswechsel selbst steht in test_mainscreen.py).
    assert a['date'] in (before, datetime.now().strftime('%d.%m.%Y'))
    assert re.fullmatch(r'\d{2}:\d{2}:\d{2}', a['time'])
    # Die Uhr laeuft weiter.
    assert b['time'] != a['time']
    match = re.fullmatch(r'FPS: (\d+\.\d) - Frame (\d+)', b['fps'])
    assert match and float(match.group(1)) > 0 and int(match.group(2)) > 1


# ------------------------------------------------------ Infozeile im Fuss

def test_tipp_auf_den_fussbereich_blendet_infos_ein_und_aus(gradient, monkeypatch):
    import sysinfo
    monkeypatch.setattr(sysinfo, 'ram_percent', lambda: 9.2)
    monkeypatch.setattr(sysinfo, 'cpu_temperature', lambda: 81.0)
    monkeypatch.setattr(sysinfo, 'load_average', lambda: (1.0, 0.5, 0.25))
    probes = {}
    run_app(gradient, [(1.5, '-PROBE-start-'), (0.1, '-INFO-'), (1.5, '-PROBE-an-'),
                       (0.1, '-INFO-'), (0.3, '-PROBE-aus-')], monkeypatch, probes=probes)
    start, an, aus = probes['start'], probes['an'], probes['aus']
    assert not start['fps_visible'] and start['info'] == ' |  | '
    assert an['fps_visible'] and an['fps'].startswith('FPS')
    assert re.fullmatch(r'CPU (\d+|-) % {3}RAM 9 % \| 81 °C \| Last 1,00 0,50 0,25', an['info'])
    assert not aus['fps_visible'] and aus['info'] == ' |  | '
    # Das Bedienfeld bleibt bedienbar wie vorher.
    assert an['clickable'] == start['clickable']

