import sys
import threading
import time
from threading import Lock

import av

from logutil import mask_credentials


def log_version():
    # PyAV ist nicht auf eine Version festgelegt (siehe requirements.txt); die
    # installierte Version steht deshalb im Journal.
    print(f"camera.py: PyAV {av.__version__}", file=sys.stderr)


# Timeouts fuer av.open() in Sekunden (Verbindungsaufbau, Lesen). Ein
# aufgebauter RTMP-Stream mit 2560x1920 braucht bis zum ersten Frame
# mehrere Sekunden; zwischen zwei Frames vergehen im Normalbetrieb dagegen
# nur Bruchteile einer Sekunde. Ein Timeout bricht den blockierenden Aufruf
# mit einem FFmpegError ab, _buffer_loop verbindet dann neu.
OPEN_TIMEOUT_SEC = 20
READ_TIMEOUT_SEC = 5

# Kein neuer Frame seit so vielen Sekunden: main.py beendet den Prozess,
# play_it/systemd starten neu (siehe README). Die Kamera selbst versucht
# endlos neu zu verbinden und gibt nie auf.
STREAM_STALE_TIMEOUT_SEC = 10


# Laengere Frist nur bis zum ersten Frame nach dem App-Start: der erste
# Verbindungsaufbau kann mehrere Versuche brauchen. Fuer einen Ausfall im
# laufenden Betrieb gilt das knappere STREAM_STALE_TIMEOUT_SEC.
STREAM_STARTUP_TIMEOUT_SEC = 30

def _close_quietly(container):
    # Ein Fehler beim Schliessen einer ohnehin verworfenen Verbindung darf
    # weder stop() noch den Lesethread abbrechen.
    if container is None:
        return
    try:
        container.close()
    except Exception as e:
        print(f"camera.py: Fehler beim Schliessen ignoriert ({type(e).__name__})", file=sys.stderr)


class Camera:
    def __init__(self, rtsp_link, reconnect_delay=2, crop_region=None):
        self.rtsp_link = rtsp_link
        self.reconnect_delay = reconnect_delay
        self.crop_region = crop_region
        self.last_frame = None
        # Unbeschnittenes Bild fuer den Ausschnitte-Editor: dort muessen sich
        # Ausschnitte auch ueber die bisherigen Grenzen hinaus setzen lassen.
        self.last_frame_full = None
        self.frame_id = 0
        self.last_frame_time = time.monotonic()
        self.start_time = time.monotonic()
        self.lock = Lock()
        self._stop_event = threading.Event()
        self._container = None

        self._thread = threading.Thread(target=self._buffer_loop, name="rtsp_read_thread", daemon=True)
        self._thread.start()

    def stop(self):
        # Beendet den Lesethread und die Verbindung, z.B. beim Stand-Wechsel im
        # Assistenten. Sonst versuchte der alte Thread endlos, die verworfene URL
        # zu erreichen.
        self._stop_event.set()
        with self.lock:
            container = self._container
        # Ausserhalb des Locks schliessen: das unterbricht ein blockierendes
        # next(frame_iter), stop() muss nicht auf den naechsten Frame warten.
        _close_quietly(container)

    def set_crop_region(self, crop_region):
        # Unter dem Lock, das auch _buffer_loop beim Lesen nutzt.
        with self.lock:
            self.crop_region = crop_region

    def snapshot(self):
        # frame_id, last_frame_time und start_time aus einem Moment, nicht
        # einzeln gelesen, waehrend der Lesethread weiterlaeuft.
        with self.lock:
            return self.frame_id, self.last_frame_time, self.start_time

    def _open_container(self):
        # options={"rtmp_live": "live"} bewusst weggelassen: fuehrt mit der auf
        # Stand 1 installierten ffmpeg-Version (4.3.9+rpt1) zu einem Segfault
        # in av.open() -- vermutlich ein Options-Dict-Bug in PyAV 10.0.0.
        container = av.open(self.rtsp_link, timeout=(OPEN_TIMEOUT_SEC, READ_TIMEOUT_SEC))
        vstream = container.streams.video[0]
        # SLICE-Threading spart auf dem Pi 4 rund 10-13 % CPU-Zeit pro Frame.
        vstream.codec_context.thread_type = "SLICE"
        vstream.codec_context.thread_count = 4
        return container

    def _buffer_loop(self):
        container = None
        frame_iter = None
        while not self._stop_event.is_set():
            try:
                if container is None:
                    connect_started = time.monotonic()
                    container = self._open_container()
                    with self.lock:
                        self._container = container
                    frame_iter = container.decode(video=0)
                    print(f"camera.py: Container geoeffnet nach {time.monotonic() - connect_started:.1f}s", file=sys.stderr)
                av_frame = next(frame_iter)
                frame = av_frame.to_ndarray(format="bgr24")
            except Exception as e:
                # Jeder Fehler fuehrt zu einem neuen Verbindungsversuch, auch
                # ein unerwarteter (z.B. aus to_ndarray). Sonst endete der
                # Lesethread still, und erst die Stale-Erkennung in main.py
                # beendete die App mit irrefuehrender Meldung.
                if self._stop_event.is_set():
                    # stop() hat den Container absichtlich geschlossen, um
                    # genau dieses next(frame_iter) zu unterbrechen - kein
                    # echter Verbindungsfehler, kein Retry noetig.
                    break
                # Jeden Fehlschlag mit Typ und Meldung ins Journal, fuer die
                # Fehlersuche (z.B. bei unerwarteten Neustarts). PyAV nimmt die URL in die
                # Meldung auf, die Zugangsdaten werden maskiert.
                print(f"camera.py: Verbindungs-/Decode-Fehler ({type(e).__name__}: {mask_credentials(e)}), naechster Versuch in {self.reconnect_delay}s", file=sys.stderr)
                _close_quietly(container)
                container = None
                with self.lock:
                    self._container = None
                frame_iter = None
                self._stop_event.wait(self.reconnect_delay)
                continue

            full_frame = frame
            with self.lock:
                crop_region = self.crop_region
            if crop_region is not None:
                x0, y0, x1, y1 = crop_region
                frame = frame[y0:min(y1, frame.shape[0]), x0:min(x1, frame.shape[1])]
            with self.lock:
                self.last_frame = frame
                self.last_frame_full = full_frame
                self.frame_id += 1
                self.last_frame_time = time.monotonic()

        # Regulaeres Ende (stop() kam zwischen zwei Frames): der Container ist
        # meist schon geschlossen, ein zweites close() schadet nicht.
        _close_quietly(container)

    def get_frame(self, full=False):
        # Nur die Referenz unter dem Lock holen, kopiert wird ausserhalb: der
        # Lesethread ersetzt die Bilder nur, er veraendert sie nie, und muss
        # so nicht auf das Kopieren warten.
        with self.lock:
            src = self.last_frame_full if full else self.last_frame
        return None if src is None else src.copy()

    def is_stale(self, timeout, startup_timeout=None):
        # True, wenn seit mehr als timeout Sekunden kein Frame kam. Die Kamera
        # haengt nie, sie versucht endlos neu zu verbinden; main.py braucht dieses
        # Signal, um den Prozess irgendwann zu beenden (siehe play_it/README).
        # Vor dem ersten Frame gilt startup_timeout: der erste Verbindungsaufbau
        # darf laenger dauern, ohne als Ausfall zu gelten.
        with self.lock:
            if self.frame_id == 0:
                grace = startup_timeout if startup_timeout is not None else timeout
                return (time.monotonic() - self.start_time) > grace
            return (time.monotonic() - self.last_frame_time) > timeout
