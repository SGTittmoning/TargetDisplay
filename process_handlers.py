"""Prozessweite Handler fuer den Betrieb als systemd-Dienst.

Nur beim Start als Programm einrichten (main.py, __main__), nicht beim
Import: Tests und Werkzeuge importieren main.py, ohne dass ein Absturz den
ganzen Prozess per os._exit beenden soll.
"""
import os
import signal
import sys
import traceback


def handle_sigterm(signum, frame):
    # Ohne eigenen Handler ist im Journal nicht sichtbar, ob main.py ein
    # SIGTERM (z.B. von "systemctl restart") ueberhaupt erreicht. Reagiert
    # main.py nicht rechtzeitig, beendet systemd es nach TimeoutStopSec per
    # SIGKILL; das gilt als Fehler und loest OnFailure=/den Reboot-Guard aus.
    print("SIGTERM empfangen, beende main.py.", file=sys.stderr)
    sys.exit(0)


def handle_uncaught(exc_type, exc_value, exc_tb):
    # Traceback ins Journal und Exit-Code 3: in der Zeile "TargetDisplay
    # beendet (exit N)" (play_it) unterscheidbar von einem regulaeren Ende
    # (0) und einem Stream-Ausfall (2). os._exit, weil ein Exit-Code aus dem
    # excepthook heraus sonst nicht durchschlaegt.
    traceback.print_exception(exc_type, exc_value, exc_tb)
    sys.stderr.flush()
    os._exit(3)


def install():
    signal.signal(signal.SIGTERM, handle_sigterm)
    sys.excepthook = handle_uncaught
