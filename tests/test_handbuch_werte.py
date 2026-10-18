"""Zahlen, die das Benutzerhandbuch oder die Kommentare der systemd-Units
zusagen, als feste Werte. Die übrigen Tests rechnen mit den Konstanten selbst;
eine versehentlich geänderte Konstante fiele dort nicht auf."""
import os
import re

import camera
import main
import pinlock
import replay
import screenlogic
import settings_store
import sysinfo
import timerlib
import transformlib

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_timer():
    # 7 s rote Vorbereitung, 3 s Stopp; 5 x 3 Sek.: 3 s Sicht, 7 s Pause.
    assert (timerlib.PREP_TIME, timerlib.STOP_TIME) == (7, 3)
    assert (timerlib.SERIES_SHOW, timerlib.SERIES_HIDE, timerlib.SERIES_LOOPS) == (3, 7, 5)
    assert timerlib.SINGLE_SHOW == {timerlib.TIMER_20: 20, timerlib.TIMER_10: 10}
    assert timerlib.TITLES == {timerlib.TIMER_5_3_7: '5 x 3 Sek.', timerlib.TIMER_20: '20 Sek.',
                               timerlib.TIMER_10: '10 Sek.'}


def test_replay():
    # Start-, Trenn- und Schlussbild je 1 s; Verzoegerung 1 s, Rahmen 600 MB.
    assert (replay.START_SEC, replay.SEPARATOR_SEC, replay.END_SEC) == (1.0, 1.0, 1.0)
    assert replay.DEFAULT_LATENCY_SEC == 1.0
    assert replay.MAX_LATENCY_SEC == 3.0
    assert replay.DEFAULT_BUDGET_MB == 600
    assert replay.MAX_BUDGET_SHARE == 0.4


def test_pin():
    # 4-6 Ziffern, 1234 zu einfach; Wartezeit 1, 4, 7 ... hoechstens 60 s,
    # Ruecksetzen nach 10 Minuten.
    assert (settings_store.PIN_MIN_LEN, settings_store.PIN_MAX_LEN) == (4, 6)
    assert settings_store.DEFAULT_PIN == '1234'
    assert (pinlock.FIRST_DELAY_SEC, pinlock.STEP_SEC, pinlock.MAX_DELAY_SEC) == (1, 3, 60)
    assert pinlock.IDLE_RESET_SEC == 600


def test_zoom_und_blinken():
    # Tipp vergroessert dreifach (Maximum sechsfach); Blinken wechselt jede Sekunde.
    assert transformlib.VIDEO_ZOOM_MAX == 6.3
    assert screenlogic.STEP_SHARE == 0.5
    assert main.BLINK_SWITCH_SEC == 1
    assert (screenlogic.PAN_EDGE_LOW, screenlogic.PAN_EDGE_HIGH) == (30, 70)


def test_kamera():
    # Hinweis im Assistenten nach 30 s; im Betrieb Neustart nach 10 s ohne Bild.
    assert camera.STREAM_STARTUP_TIMEOUT_SEC == 30
    assert camera.STREAM_STALE_TIMEOUT_SEC == 10
    assert (camera.OPEN_TIMEOUT_SEC, camera.READ_TIMEOUT_SEC) == (20, 5)


def test_temperaturstufen():
    assert (sysinfo.TEMP_WARN_C, sysinfo.TEMP_HOT_C) == (70, 80)


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def shell_value(text, name):
    return re.search(rf'^{name}=(\S+)$', text, re.M).group(1)


def test_eskalation():
    # Hoechstens 5 Reboots; Zaehler zurueck nach 120 s stabilem Lauf mit
    # weniger als 3 Neustarts; Wiederholung und Ruecksetzen alle 10 Minuten.
    assert shell_value(read('ansible', 'files', 'targetdisplay-reboot-guard.sh'), 'MAX_REBOOTS') == '5'
    reset = read('ansible', 'files', 'targetdisplay-reboot-count-reset.sh')
    assert (shell_value(reset, 'MIN_STABLE_SEC'), shell_value(reset, 'MAX_RESTARTS')) == ('120', '3')
    unit = read('ansible', 'templates', 'targetdisplay.service.j2')
    assert (shell_value(unit, 'StartLimitIntervalSec'), shell_value(unit, 'StartLimitBurst')) == ('300', '5')
    assert shell_value(read('ansible', 'templates', 'targetdisplay-retry.timer.j2'), 'OnUnitActiveSec') == '10min'
    reset_timer = read('ansible', 'templates', 'targetdisplay-reboot-count-reset.timer.j2')
    assert (shell_value(reset_timer, 'OnBootSec'), shell_value(reset_timer, 'OnUnitActiveSec')) == ('5min', '10min')
