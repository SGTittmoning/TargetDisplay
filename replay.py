"""Aufnahme der Gruenphasen einer Timer-Serie und Wiedergabe als Replay.

Gespeichert wird das rohe, auf den Scheibenbereich zugeschnittene Kamerabild
(wie die Blink-Referenz), nicht das entzerrte Anzeigebild - so wirken Ganze
Scheibe, Innen Scheibe und der manuelle Zoom im Replay genau wie live. Die
Aufnahme liegt nur im RAM.
"""
import math
import sys
from collections import namedtuple

# Rotes Startbild mit dem Timer-Modus vor dem ersten Durchgang.
START_SEC = 1.0

# Rotes Trennbild zwischen zwei Durchgaengen der Serie.
SEPARATOR_SEC = 1.0

# Rotes Schlussbild, danach endet das Replay von selbst.
END_SEC = 1.0

# Kamerabilder kommen ueber RTMP verspaetet an. Die Aufnahme verschiebt die
# Gruenfenster um diesen Wert, sonst fehlten die letzten Schuesse. Begrenzt
# auf die rote Stopp-Phase nach dem letzten Durchgang (timerlib.STOP_TIME),
# denn nur so lange laeuft der Timer noch und nimmt auf.
DEFAULT_LATENCY_SEC = 1.0
MAX_LATENCY_SEC = 3.0

# Obergrenze fuer den RAM-Bedarf einer Aufnahme. Bei einem grossen
# Scheibenausschnitt wird statt jedes Bildes nur jedes zweite (vierte, ...)
# behalten, die Aufnahme deckt aber weiter die ganze Gruenphase ab.
DEFAULT_BUDGET_MB = 600

# Hoechstens dieser Anteil des Arbeitsspeichers geht an die Aufnahme. Auf
# einem Pi mit 1 GB bliebe mit vollem Rahmen sonst zu wenig fuer App und
# System; ab 2 GB greift die Grenze beim Standardwert nicht.
MAX_BUDGET_SHARE = 0.4

PROC_MEMINFO = '/proc/meminfo'

# kind: 'start', 'frame', 'separator' oder 'end'; segment: Index des Durchgangs (0-basiert);
# offset: Sekunden seit Beginn des Durchgangs; frame: Rohbild oder None;
# key: wechselt genau dann, wenn ein anderes Bild zu zeigen ist.
ReplayView = namedtuple('ReplayView', 'kind segment offset frame key')


def read_settings(cfg):
    """(Verzoegerung in s, Speicherrahmen in Bytes) aus config.yml.

    Ein ungueltiger Wert faellt mit Hinweis im Journal auf den Standard
    zurueck, statt die App beim Start oder beim ersten Timer abzubrechen.
    """
    def number(key, default, convert, valid):
        value = cfg.get_property_with_default(key, default)
        try:
            value = convert(value)
        except (TypeError, ValueError):
            value = None
        if value is None or not valid(value):
            print(f"replay: {key} ungültig, Standard {default} gilt.", file=sys.stderr)
            return default
        return value

    latency = number('replay.latency_sec', DEFAULT_LATENCY_SEC, float, math.isfinite)
    budget = number('replay.budget_mb', DEFAULT_BUDGET_MB, int, lambda v: v > 0) * 1024 * 1024
    total = mem_total_bytes()
    if total is not None and budget > total * MAX_BUDGET_SHARE:
        budget = int(total * MAX_BUDGET_SHARE)
        print(f"replay: Speicherrahmen auf {budget // (1024 * 1024)} MB begrenzt "
              f"({MAX_BUDGET_SHARE:.0%} von {total // (1024 * 1024)} MB RAM).", file=sys.stderr)
    return latency, budget


def mem_total_bytes():
    """Gesamter Arbeitsspeicher in Bytes, None wenn nicht lesbar."""
    try:
        with open(PROC_MEMINFO, encoding='ascii') as f:
            for line in f:
                if line.startswith('MemTotal:'):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return None


class Recording:
    def __init__(self, windows, latency=DEFAULT_LATENCY_SEC, budget_bytes=DEFAULT_BUDGET_MB * 1024 * 1024):
        self.windows = list(windows)
        self.latency = min(max(float(latency), 0.0), MAX_LATENCY_SEC)
        self.budget_bytes = budget_bytes
        # Je Durchgang eine Liste von (offset, frame), zeitlich aufsteigend.
        self.segments = [[] for _ in self.windows]
        self.bytes = 0
        self.step = 1
        self._offered = 0
        # Spaetester Kamerazeitpunkt, den die Aufnahme gesehen hat (Sekunden
        # ab Timer-Start, um die Verzoegerung bereinigt).
        self._clock = None

    def is_complete(self, elapsed):
        """True, sobald `elapsed` (ab Timer-Start) hinter dem letzten Gruenfenster liegt."""
        return elapsed - self.latency >= self.windows[-1][1]

    def add(self, frame, elapsed):
        """Nimmt ein Kamerabild auf, das `elapsed` Sekunden nach Timer-Start ankam.

        Liefert True, wenn das Bild gespeichert wurde.
        """
        t = elapsed - self.latency
        self._clock = t if self._clock is None else max(self._clock, t)
        i = self._window_index(t)
        if i is None:
            return False
        start = self.windows[i][0]
        self._offered += 1
        if (self._offered - 1) % self.step:
            return False
        while self.bytes + frame.nbytes > self.budget_bytes and self.bytes > 0:
            self._thin()
            if (self._offered - 1) % self.step:
                return False
        if self.bytes + frame.nbytes > self.budget_bytes:
            return False
        self.segments[i].append((t - start, frame))
        self.bytes += frame.nbytes
        return True

    def _window_index(self, t):
        for i, (start, end) in enumerate(self.windows):
            if start <= t < end:
                return i
        return None

    def _thin(self):
        # Jedes zweite Bild verwerfen und kuenftig nur noch jedes zweite
        # (bzw. vierte, ...) annehmen.
        self.step *= 2
        for k, seg in enumerate(self.segments):
            self.segments[k] = seg[::2]
        self.bytes = sum(f.nbytes for seg in self.segments for _, f in seg)

    def has_frames(self):
        return any(self.segments)

    def frame_count(self):
        return sum(len(seg) for seg in self.segments)

    def _segment_lengths(self):
        # Vollstaendig vergangene Durchgaenge zaehlen mit ihrer vollen Dauer.
        # Wurde der Timer mitten in einem Durchgang gestoppt, endet dieser
        # mit seinem letzten Bild. Durchgaenge ohne jedes Bild am Ende fallen
        # weg.
        lengths = []
        for (start, end), seg in zip(self.windows, self.segments):
            if self._clock is not None and self._clock >= end:
                lengths.append(end - start)
            elif seg:
                lengths.append(seg[-1][0])
            else:
                lengths.append(0.0)
        while lengths and not self.segments[len(lengths) - 1]:
            lengths.pop()
        return lengths

    def duration(self):
        lengths = self._segment_lengths()
        if not lengths:
            return 0.0
        return START_SEC + sum(lengths) + SEPARATOR_SEC * (len(lengths) - 1) + END_SEC

    def view(self, position):
        """Was zur Abspielzeit `position` (Sekunden) zu zeigen ist.

        Vor dem ersten Durchgang steht das rote Startbild, nach dem letzten
        das rote Schlussbild, danach (Replay zu Ende) None.
        """
        lengths = self._segment_lengths()
        if not lengths:
            return None
        position = max(position, 0.0)
        if position < START_SEC:
            return ReplayView('start', 0, 0.0, None, ('start',))
        position -= START_SEC
        for i, length in enumerate(lengths):
            if i > 0:
                if position < SEPARATOR_SEC:
                    return ReplayView('separator', i, 0.0, None, ('sep', i))
                position -= SEPARATOR_SEC
            if position < length:
                return self._frame_view(i, position)
            position -= length
        if position < END_SEC:
            last = len(lengths) - 1
            return ReplayView('end', last, lengths[last], None, ('end',))
        return None

    def _frame_view(self, segment, offset):
        seg = self.segments[segment]
        if not seg:
            return ReplayView('frame', segment, offset, None, ('none', segment))
        # Spaetestes Bild, das zum Zeitpunkt `offset` schon aufgenommen war,
        # vor dem ersten Bild das erste.
        idx = 0
        for k, (t, _) in enumerate(seg):
            if t <= offset:
                idx = k
            else:
                break
        return ReplayView('frame', segment, offset, seg[idx][1], ('frame', segment, idx))


def counter_text(view, segment_total):
    """Zaehler fuer die Replay-Anzeige, Zehntelsekunden mit Dezimalkomma."""
    seconds = f'{view.offset:.1f}'.replace('.', ',') + ' s'
    if segment_total > 1:
        return f'Durchgang {view.segment + 1}/{segment_total}  {seconds}'
    return seconds
