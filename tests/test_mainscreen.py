"""Ablauf des Hauptbildschirms in simulierter Zeit (siehe screen_harness):
Timer, Replay, Blinken und Zoom ohne Tk und ohne Wartezeiten."""
from screen_harness import Screen, gray

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
    return [d for d in s.window.draws[start:] if is_replay(d) and tuple(d[200, 150]) != (0, 0, 255)]


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
