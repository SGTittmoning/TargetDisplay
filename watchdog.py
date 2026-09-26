"""Entscheidung, wann ein ausbleibender Kamera-Stream zum Prozessende fuehrt."""


def stale_check_due(display_video, display_timer, now, resumed_at, grace):
    """True, wenn die Stream-Staleness geprueft werden soll.

    Nur waehrend die Kamerabild-Anzeige aktiv ist: Timer-Serien und der
    Blank-Screen ("Video aus") brauchen die Kamera nicht und duerfen von einem
    Stream-Ausfall nicht unterbrochen werden. Nach dem Wiedereinschalten des
    Bildes bekommt der Stream `grace` Sekunden, um wieder Frames zu liefern.
    """
    if not display_video or display_timer:
        return False
    return (now - resumed_at) > grace
