"""Wartezeit nach fehlerhaften PIN-Eingaben.

Keine Sperre, sondern eine mit jedem Fehlversuch leicht ansteigende Pause
(1 s, 4 s, 7 s, ... bis maximal 60 s), die systematisches Durchprobieren am
Geraet bremst. Der Zaehler wird bei richtiger Eingabe und nach laengerer
Ruhe zurueckgesetzt und lebt nur im Arbeitsspeicher.
"""
import time

MAX_DELAY_SEC = 60
FIRST_DELAY_SEC = 1
STEP_SEC = 3
IDLE_RESET_SEC = 600


class PinLimiter:
    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._failures = 0
        self._last_failure = None

    def register_failure(self):
        """Zaehlt einen Fehlversuch und liefert die Wartezeit in Sekunden."""
        now = self._clock()
        if self._last_failure is not None and now - self._last_failure > IDLE_RESET_SEC:
            self._failures = 0
        self._failures += 1
        self._last_failure = now
        return min(MAX_DELAY_SEC, FIRST_DELAY_SEC + STEP_SEC * (self._failures - 1))

    def register_success(self):
        self._failures = 0
        self._last_failure = None
