"""Ablauf des Hauptbildschirms in simulierter Zeit (siehe screen_harness):
Timer, Replay, Blinken und Zoom ohne Tk und ohne Wartezeiten."""
import re

import numpy as np
import pytest
from screen_harness import BLACK, RED, Screen, below_center, gray

import overlays
import timerlib


def reference(s):
    """Grauwert des aktuellen Blink-Referenzbilds."""
    return gray(s.screen.view.render(s.screen.blink_ref))


def is_replay(frame):
    return frame[frame.shape[0] // 2, 1].tolist() == list(overlays.REPLAY_COLOR)


def test_timer_endet_genau_einmal(monkeypatch):
    s = Screen(monkeypatch)
    stops = []
    real_stop = s.screen.timer_stop
    monkeypatch.setattr(s.screen, 'timer_stop', lambda *a: (stops.append(s.mode), real_stop(*a)))
    s.send('-TIMER_10-').run(25)
    # Vorbereitung 7 s, Sichtzeit 10 s, Stopp 3 s: danach genau ein Ende.
    assert stops == ['timer']
    assert s.mode == 'live'


def test_replay_endet_von_selbst_zurueck_im_livebild(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_10-').run(21).send('-REPLAY-').run(1)
    assert s.mode == 'replay'
    s.run(15)
    assert s.mode == 'live' and s.window.controls['has_recording']


def test_blinken_wechselt_jede_sekunde(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-BLINK_START-').run(0.2)
    ref = reference(s)
    start = len(s.window.draws)
    s.run(4)
    shows_ref = [gray(d) == ref for d in s.window.draws[start:]]
    changes = sum(1 for a, b in zip(shows_ref, shows_ref[1:]) if a != b)
    # In 4 s viermal zwischen Referenz und Livebild gewechselt (+-1 am Rand).
    assert 3 <= changes <= 5


def test_blink_referenz_bleibt_ueber_timer_und_replay(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-BLINK_START-').run(0.2)
    ref = reference(s)
    s.send('-TIMER_10-').run(21).send('-REPLAY-').run(1).send('-REPLAY_END-').run(3)
    after = [gray(d) for d in s.window.draws[-18:]]
    assert after.count(ref) >= 3


def test_blinken_auch_bei_innen_scheibe(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-DETAIL_VIDEO-').send('-BLINK_START-').run(0.2)
    ref = reference(s)
    start = len(s.window.draws)
    s.run(3)
    values = [gray(d) for d in s.window.draws[start:]]
    assert values.count(ref) >= 3 and max(values) > ref


def replay_frames(s, seconds):
    start = len(s.window.draws)
    s.run(seconds)
    # Nur Aufnahmebilder: nicht rot (Start-/Trennbild).
    return [d for d in s.window.draws[start:] if is_replay(d) and below_center(d) != RED]


def test_zoom_wirkt_im_replay(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_10-').run(21).send('-REPLAY-').run(2)
    full = replay_frames(s, 1)
    s.send('-DETAIL_VIDEO-')
    detail = replay_frames(s, 1)
    s.send('-VIDEO-', {'-VIDEO-': (80, 50)})
    tapped = replay_frames(s, 1)
    # Kanal 1 = x-Verlauf im Kamerabild: Innen Scheibe zeigt einen engeren
    # Bereich, der Tipp-Zoom einen noch engeren, nach rechts verschoben.
    span = [int(f[200, 225, 1]) - int(f[200, 75, 1]) for f in (full[-1], detail[-1], tapped[-1])]
    assert span[0] > span[1] > span[2] > 0
    assert int(tapped[-1][200, 150, 1]) > int(detail[-1][200, 150, 1])


def test_latenz_aus_der_konfiguration_kommt_an(monkeypatch):
    s = Screen(monkeypatch, replay_settings=(2.5, 300 * 1024 * 1024))
    s.send('-TIMER_10-').run(0.1)
    assert s.screen.recording.latency == 2.5
    assert s.screen.recording.budget_bytes == 300 * 1024 * 1024


def test_timer_zeigt_countdown_und_ende(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_20-')
    states = []
    for _ in range(31):
        s.run(1)
        states.append(s.screen.last_timer_state)
    colors = [st.color for st in states]
    # 7 s rot, 20 s gruen mit Countdown ab 20, am Ende wieder rot.
    assert colors[0] == 'red' and 'green' in colors
    assert [st.countdown for st in states if st.color == 'green'][:2] == [20, 19]
    assert timerlib.SINGLE_SHOW[timerlib.TIMER_20] == 20


def timer_states(s, seconds):
    """Alle Anzeigezustaende des Timers in der Reihenfolge ihres Auftretens."""
    seen = []

    def sample():
        state = s.screen.last_timer_state
        if state is not None and (not seen or seen[-1] != state):
            seen.append(state)
    s.run(seconds, each=sample)
    return seen


def test_serie_zeigt_durchgangszahl_und_countdown(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_5_3_7-')
    seen = timer_states(s, 75)
    # 7 s Vorbereitung ohne Nummer, dann fuenf Durchgaenge mit 3 s Gruen
    # (Countdown 3, 2, 1) und je 7 s Pause; nach dem fuenften Gruen entfaellt
    # die Pause, der Stopp zeigt weder Nummer noch Countdown.
    assert seen[0] == timerlib.timer_state(timerlib.TIMER_5_3_7, 0)
    assert seen[0].color == 'red' and seen[0].number is None and seen[0].countdown == 7
    green = [(st.number, st.countdown) for st in seen if st.color == 'green']
    assert [n for n, _ in green if _ == 3] == [1, 2, 3, 4, 5]
    assert {c for _, c in green} == {1, 2, 3}
    assert all(st.number is not None for st in seen if st.color == 'green')
    assert seen[-1].color == 'red' and seen[-1].number is None and seen[-1].countdown is None
    pause = [st for st in seen if st.color == 'red' and st.number == 1]
    assert pause[0].countdown == 7 and pause[-1].countdown == 1


def test_einzeltimer_ohne_durchgangszahl(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_20-')
    seen = timer_states(s, 31)
    green = [st for st in seen if st.color == 'green']
    assert green and not any(st.number for st in green)
    assert green[0].countdown == 20 and green[-1].countdown == 1


def drawn_texts(monkeypatch):
    texts = []
    real = overlays.cv2.putText
    monkeypatch.setattr(overlays.cv2, 'putText',
                        lambda img, text, *a, **k: (texts.append(text), real(img, text, *a, **k))[1])
    return texts


def test_replay_zeigt_titel_trenntext_und_zaehler(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_5_3_7-').run(72).send('-REPLAY-')
    texts = drawn_texts(monkeypatch)
    s.run(25)
    replay = texts[texts.index('5 x 3 Sek.'):]
    assert 'Durchgang 2/5' in replay
    counters = [t for t in replay if t.startswith('Durchgang ') and t.endswith(' s')]
    assert re.fullmatch(r'Durchgang 1/5  0,\d s', counters[0])
    assert re.fullmatch(r'Durchgang 5/5  \d,\d s', counters[-1])
    # Der Zaehler beginnt fuer jeden Durchgang von vorn.
    first_2 = next(t for t in counters if t.startswith('Durchgang 2/5'))
    assert first_2.startswith('Durchgang 2/5  0,')


def red(frame):
    return below_center(frame) == RED


def test_replay_serie_mit_trennbild(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_5_3_7-').run(72).send('-REPLAY-')
    start = len(s.window.draws)
    s.run(25)
    kinds = []
    for d in s.window.draws[start:]:
        if not is_replay(d):
            continue
        kind = 'rot' if red(d) else 'bild'
        if not kinds or kinds[-1] != kind:
            kinds.append(kind)
    # Startbild, fuenf Durchgaenge mit Trennbildern dazwischen, Schlussbild.
    assert kinds == ['rot', 'bild'] + ['rot', 'bild'] * 4 + ['rot']
    assert not is_replay(s.window.draws[-1])


def test_replay_kein_bild_bei_ausgefallener_kamera(monkeypatch):
    s = Screen(monkeypatch)
    # Im ersten Durchgang (7 bis 10 s, aufgenommen 1 s verzoegert) liefert die
    # Kamera nichts, danach wieder Bilder.
    s.cap.blank = True
    s.send('-TIMER_5_3_7-').run(12)
    s.cap.blank = False
    s.run(60).send('-REPLAY-').run(1.5)
    start = len(s.window.draws)
    s.run(25)
    shown = [d for d in s.window.draws[start:] if is_replay(d) and not red(d)]
    # "kein Bild" ist schwarz, danach folgt das Kamerabild.
    assert any(below_center(d) == BLACK for d in shown)
    assert any(below_center(d) != BLACK for d in shown)


def test_naechster_timer_ersetzt_die_aufnahme(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_10-').run(21)
    assert s.window.controls['has_recording']
    # Der zweite Timer wird vor der Gruenphase gestoppt: die neue, leere
    # Aufnahme ersetzt die alte.
    s.send('-TIMER_10-').run(2).send('-TIMER_STOP-').run(1)
    assert s.mode == 'live' and not s.window.controls['has_recording']


def test_tippen_im_timer_und_bei_video_aus_zoomt_nicht(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-TIMER_10-').run(1).send('-VIDEO-', {'-VIDEO-': (50, 50)}).run(1)
    assert s.screen.view.center is None
    s.run(20).send('-TOGGLEVIDEO-').run(0.2).send('-VIDEO-', {'-VIDEO-': (50, 50)}).run(0.2)
    assert s.mode == 'blank' and s.screen.view.center is None


def test_blinken_referenz_nimmt_ein_neues_bezugsbild_auf(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-BLINK_START-').run(0.5)
    before = reference(s)
    s.run(2).send('-BLINK_REF-').run(0.5)
    assert reference(s) != before


def test_uhr_und_geraetewerte_laufen_in_jedem_modus(monkeypatch):
    s = Screen(monkeypatch)
    s.send('-INFO-').run(0.1)
    stamps = []

    def phase(seconds):
        before = len(s.window.infos)
        s.run(seconds)
        shown = [args[0] for args, _ in s.window.infos[before:]]
        stamps.append((s.mode, shown.count(True)))
    phase(3)
    s.send('-TIMER_10-')
    phase(5)
    s.run(16).send('-REPLAY-')
    phase(1.5)
    s.send('-REPLAY_END-').send('-TOGGLEVIDEO-')
    phase(3)
    # Je Sekunde ein neuer Wert, auch im Timer, Replay und bei Video aus.
    assert [mode for mode, _ in stamps] == ['live', 'timer', 'replay', 'blank']
    assert all(count >= 1 for _, count in stamps[:2] + stamps[3:]), stamps
    assert stamps[0][1] >= 2 and stamps[1][1] >= 4 and stamps[3][1] >= 2


def test_uhr_wechselt_um_mitternacht_das_datum(monkeypatch):
    s = Screen(monkeypatch)
    dates, times = [], []
    s.run(10, each=lambda: (dates.append(s.window['-DATE-'].value), times.append(s.window['-TIME-'].value)))
    assert dates[0] == '18.10.2026' and dates[-1] == '19.10.2026'
    assert times[0] == '23:59:55' and times[-1] in ('00:00:04', '00:00:05')


def test_zoomfelder_im_bild_schalten_die_stufen(monkeypatch):
    s = Screen(monkeypatch)
    rects = s.screen.view._control_rects()
    cx = lambda r: ((r[0] + r[2]) / 2 * 100 / 300, (r[1] + r[3]) / 2 * 100 / 300)  # noqa: E731
    plus, minus = cx(rects['in']), cx(rects['out'])
    s.send('-VIDEO-', {'-VIDEO-': plus}).run(0.2)
    assert s.screen.view.step == 1 and s.window.controls['zoom_center'] is not None
    s.send('-VIDEO-', {'-VIDEO-': plus}).run(0.2)
    assert s.screen.view.step == 2
    s.send('-VIDEO-', {'-VIDEO-': minus}).run(0.2)
    assert s.screen.view.step == 1
    s.send('-VIDEO-', {'-VIDEO-': minus}).run(0.2)
    assert s.screen.view.step == 0 and s.window.controls['zoom_center'] is None
    # Ein Tipp aufs Feld vergroessert nicht zusaetzlich um die getippte Stelle.
    assert s.screen.view.center is None


def test_zoomfelder_im_timer_ohne_wirkung(monkeypatch):
    s = Screen(monkeypatch)
    rects = s.screen.view._control_rects()
    x0, y0, x1, y1 = rects['in']
    s.send('-TIMER_10-').run(1).send('-VIDEO-', {'-VIDEO-': ((x0 + x1) / 6, (y0 + y1) / 6)}).run(1)
    assert s.screen.view.step == 0


def test_zoomfelder_sind_im_livebild_sichtbar(monkeypatch):
    s = Screen(monkeypatch)
    s.run(0.5)
    x0, y0, x1, y1 = s.screen.view._control_rects()['out']
    assert s.window.draws[-1][y0:y1, x0:x1].mean() < 100


def test_vergroesserung_der_innen_scheibe_aus_den_ausschnitten():
    import main
    full = np.array([[0, 0], [800, 0], [800, 800], [0, 800]])
    detail = np.array([[200, 200], [600, 200], [600, 600], [200, 600]])
    assert main.MainScreen.detail_magnification(full, detail) == 2.0
    assert main.MainScreen.detail_magnification(full, full) == 1.0


def test_maximum_passt_zur_innen_scheibe_von_stand_1():
    import main
    import transformlib
    full = np.array([[92, 662], [1052, 514], [368, 1359], [1170, 1319]])
    detail = np.array([[387, 839], [805, 792], [489, 1182], [901, 1170]])
    # Innen Scheibe ist dort gut 2,1-fach vergroessert; mit weiteren 3x
    # ergibt das das Maximum.
    magnification = main.MainScreen.detail_magnification(full, detail)
    assert magnification == pytest.approx(2.11, abs=0.01)
    assert magnification * 3 == pytest.approx(transformlib.VIDEO_ZOOM_MAX, abs=0.1)
