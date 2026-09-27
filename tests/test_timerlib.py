import pytest

import timerlib as t


def colors(timer_type):
    return [t.timer_state(timer_type, s).color for s in range(t.total_seconds(timer_type) + 1)]


def test_gesamtdauer():
    assert t.total_seconds(t.TIMER_5_3_7) == 60
    assert t.total_seconds(t.TIMER_20) == 30
    assert t.total_seconds(t.TIMER_10) == 20


def test_serie_ablauf():
    s = t.timer_state
    assert s(t.TIMER_5_3_7, 0) == ('red', None, 7, False)
    assert s(t.TIMER_5_3_7, 6) == ('red', None, 1, False)
    assert s(t.TIMER_5_3_7, 7) == ('green', 1, 3, False)
    assert s(t.TIMER_5_3_7, 9) == ('green', 1, 1, False)
    assert s(t.TIMER_5_3_7, 10) == ('red', 1, 7, False)
    assert s(t.TIMER_5_3_7, 16) == ('red', 1, 1, False)
    assert s(t.TIMER_5_3_7, 17) == ('green', 2, 3, False)
    assert s(t.TIMER_5_3_7, 47) == ('green', 5, 3, False)
    assert s(t.TIMER_5_3_7, 50) == ('red', 5, 7, False)
    assert s(t.TIMER_5_3_7, 56) == ('red', 5, 1, False)
    # Stopp-Phase nach dem letzten Durchgang
    assert s(t.TIMER_5_3_7, 57) == ('red', None, None, False)
    assert s(t.TIMER_5_3_7, 59) == ('red', None, None, False)
    assert s(t.TIMER_5_3_7, 60).finished


def test_serie_hat_fuenf_gruene_phasen():
    seq = [t.timer_state(t.TIMER_5_3_7, s) for s in range(60)]
    assert sorted({x.number for x in seq if x.number}) == [1, 2, 3, 4, 5]
    assert sum(1 for x in seq if x.color == 'green') == 15


@pytest.mark.parametrize("timer_type,show", [(t.TIMER_20, 20), (t.TIMER_10, 10)])
def test_einzeldurchgang(timer_type, show):
    s = t.timer_state
    assert s(timer_type, 0) == ('red', None, 7, False)
    assert s(timer_type, 7) == ('green', None, show, False)
    assert s(timer_type, 7 + show - 1) == ('green', None, 1, False)
    assert s(timer_type, 7 + show) == ('red', None, None, False)
    assert s(timer_type, 7 + show + 2) == ('red', None, None, False)
    assert s(timer_type, 7 + show + 3).finished


def test_negative_dauer_wird_als_start_behandelt():
    assert t.timer_state(t.TIMER_20, -5) == t.timer_state(t.TIMER_20, 0)


def test_bruchteile_werden_abgerundet():
    assert t.timer_state(t.TIMER_10, 6.99) == t.timer_state(t.TIMER_10, 6)
