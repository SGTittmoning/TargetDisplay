"""Systemwerte fuer die Infozeile im Fussbereich (CPU, RAM, Temperatur, Last).

Liest direkt aus /proc und /sys, ohne zusaetzliches Paket. Jede Funktion
liefert None, wenn der Wert nicht lesbar ist (z.B. ausserhalb eines Pi).
"""
import os

PROC_STAT = '/proc/stat'
PROC_MEMINFO = '/proc/meminfo'
THERMAL_ZONE = '/sys/class/thermal/thermal_zone0/temp'


class CpuUsage:
    """CPU-Auslastung in Prozent seit dem letzten Aufruf (alle Kerne)."""

    def __init__(self, path=PROC_STAT):
        self.path = path
        self._last = None

    def _read(self):
        with open(self.path, encoding='ascii') as f:
            values = [int(v) for v in f.readline().split()[1:]]
        # idle + iowait gelten als untaetig.
        idle = values[3] + (values[4] if len(values) > 4 else 0)
        return sum(values), idle

    def percent(self):
        try:
            total, idle = self._read()
        except (OSError, ValueError, IndexError):
            return None
        last, self._last = self._last, (total, idle)
        if last is None or total <= last[0]:
            return None
        return 100.0 * (1 - (idle - last[1]) / (total - last[0]))


def ram_percent(path=PROC_MEMINFO):
    """Belegter Arbeitsspeicher in Prozent (MemTotal - MemAvailable)."""
    try:
        with open(path, encoding='ascii') as f:
            info = {line.split(':')[0]: int(line.split()[1]) for line in f if ':' in line}
        return 100.0 * (1 - info['MemAvailable'] / info['MemTotal'])
    except (OSError, ValueError, IndexError, KeyError, ZeroDivisionError):
        return None


def cpu_temperature(path=THERMAL_ZONE):
    """CPU-Temperatur in Grad Celsius."""
    try:
        with open(path, encoding='ascii') as f:
            return int(f.read().strip()) / 1000
    except (OSError, ValueError):
        return None


def load_average():
    """Systemlast der letzten 1, 5 und 15 Minuten."""
    try:
        return os.getloadavg()
    except OSError:
        return None


# Ab hier wird die Temperatur gelb bzw. rot angezeigt. Der Pi 4 drosselt
# ab 80 °C den Takt.
TEMP_WARN_C = 70
TEMP_HOT_C = 80


def _num(value, digits=0):
    # Deutsche Schreibweise mit Dezimalkomma.
    return f'{value:.{digits}f}'.replace('.', ',')


def usage_text(cpu, ram):
    """'CPU 38 %   RAM 9 %'; fehlende Werte als '-'."""
    return '   '.join(f'{name} {"-" if v is None else _num(v)} %' for name, v in (('CPU', cpu), ('RAM', ram)))


def temperature_text(temp):
    return '' if temp is None else f'{_num(temp)} °C'


def temperature_level(temp):
    """'ok', 'warn' oder 'hot' (fuer die Farbe der Anzeige)."""
    if temp is None or temp < TEMP_WARN_C:
        return 'ok'
    return 'hot' if temp >= TEMP_HOT_C else 'warn'


def load_text(load):
    return '' if load is None else 'Last ' + ' '.join(_num(v, 2) for v in load)
