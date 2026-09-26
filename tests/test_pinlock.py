import pinlock
from pinlock import PinLimiter


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_wartezeit_steigt_leicht_an():
    limiter = PinLimiter(FakeClock())
    assert [limiter.register_failure() for _ in range(5)] == [1, 4, 7, 10, 13]


def test_wartezeit_ist_auf_eine_minute_begrenzt():
    limiter = PinLimiter(FakeClock())
    delays = [limiter.register_failure() for _ in range(40)]
    assert max(delays) == pinlock.MAX_DELAY_SEC == 60
    assert delays == sorted(delays)
    assert delays[-1] == 60


def test_richtige_eingabe_setzt_zurueck():
    limiter = PinLimiter(FakeClock())
    for _ in range(4):
        limiter.register_failure()
    limiter.register_success()
    assert limiter.register_failure() == 1


def test_zaehler_verfaellt_nach_ruhephase():
    clock = FakeClock()
    limiter = PinLimiter(clock)
    for _ in range(4):
        limiter.register_failure()
    clock.now += pinlock.IDLE_RESET_SEC + 1
    assert limiter.register_failure() == 1


def test_kurze_pause_setzt_nicht_zurueck():
    clock = FakeClock()
    limiter = PinLimiter(clock)
    limiter.register_failure()
    clock.now += 30
    assert limiter.register_failure() == 4
