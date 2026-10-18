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
