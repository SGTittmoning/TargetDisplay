"""Entscheidung, wann ein ausbleibender Kamera-Stream zum Prozessende fuehrt."""


def stale_check_due(mode, now, resumed_at, grace):
    """True, wenn die Stream-Staleness geprueft werden soll.

    Nur im Livebild (mode 'live'): Timer, Replay und "Video aus" brauchen die
    Kamera nicht und duerfen von einem Stream-Ausfall nicht unterbrochen
    werden. Nach der Rueckkehr zum Livebild bekommt der Stream `grace`
    Sekunden, um wieder Frames zu liefern.
    """
    return mode == 'live' and (now - resumed_at) > grace
