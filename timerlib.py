"""Ablauf der Wettkampf-Timer als reine Funktion der verstrichenen Zeit.

Die Dauer wird vom Aufrufer mit einer monotonen Uhr (time.monotonic())
gemessen, damit weder eine NTP-Korrektur noch eine Umstellung der
Systemzeit den Ablauf verschiebt.
"""
from collections import namedtuple

TIMER_5_3_7 = '-TIMER_5_3_7-'
TIMER_20 = '-TIMER_20-'
TIMER_10 = '-TIMER_10-'

PREP_TIME = 7   # rote Vorbereitungsphase vor dem ersten Durchgang
STOP_TIME = 3   # rote Stopp-Phase nach dem letzten Durchgang

# Serie 5x3/7: fuenf Durchgaenge mit je 3 s gruen und 7 s rot.
SERIES_SHOW = 3
SERIES_HIDE = 7
SERIES_LOOPS = 5

# Einzeldurchgaenge: Dauer der gruenen Phase.
SINGLE_SHOW = {TIMER_20: 20, TIMER_10: 10}

# color: 'red' oder 'green'; number: Nummer des Durchgangs oder None;
# countdown: verbleibende Sekunden der Phase oder None; finished: Ablauf zu Ende.
TimerState = namedtuple('TimerState', 'color number countdown finished')

_FINISHED = TimerState('red', None, None, True)


def total_seconds(timer_type):
    if timer_type == TIMER_5_3_7:
        return PREP_TIME + SERIES_LOOPS * (SERIES_SHOW + SERIES_HIDE) + STOP_TIME
    return PREP_TIME + SINGLE_SHOW[timer_type] + STOP_TIME


def timer_state(timer_type, elapsed):
    """Zustand des Timers nach `elapsed` Sekunden (ganze Sekunden, abgerundet)."""
    t = max(0, int(elapsed))

    if t < PREP_TIME:
        return TimerState('red', None, PREP_TIME - t, False)

    if timer_type == TIMER_5_3_7:
        cycle = SERIES_SHOW + SERIES_HIDE
        loop, into = divmod(t - PREP_TIME, cycle)
        if loop >= SERIES_LOOPS:
            if t - PREP_TIME - SERIES_LOOPS * cycle < STOP_TIME:
                return TimerState('red', None, None, False)
            return _FINISHED
        if into < SERIES_SHOW:
            return TimerState('green', loop + 1, SERIES_SHOW - into, False)
        return TimerState('red', loop + 1, cycle - into, False)

    show = SINGLE_SHOW[timer_type]
    if t < PREP_TIME + show:
        return TimerState('green', None, PREP_TIME + show - t, False)
    if t < PREP_TIME + show + STOP_TIME:
        return TimerState('red', None, None, False)
    return _FINISHED
