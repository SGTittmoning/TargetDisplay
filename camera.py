import sys
import threading
from threading import Lock
import time
import av

class Camera:
    def __init__(self, rtsp_link, reconnect_delay=2, crop_region=None):
        self.rtsp_link = rtsp_link
        self.reconnect_delay = reconnect_delay
        self.crop_region = crop_region
        self.last_frame = None
        # Unbeschnittene Variante desselben Frames, ausschliesslich fuer den
        # Settings-Punkte-Editor (main.py::edit_section_points) - der
        # normale Anzeige-/Warp-Pfad braucht nur den ohnehin schon eng um
        # section_full/section_detail zugeschnittenen last_frame, der
        # Editor zum NEU-Setzen der Ausschnitte muss aber das komplette
        # Kamerabild sehen koennen, auch ausserhalb der aktuellen Grenzen.
        self.last_frame_full = None
        self.last_ready = None
        self.frame_id = 0
        self.last_frame_time = time.time()
        self.start_time = time.time()
        self.lock = Lock()
        self._stop_event = threading.Event()
        self._container = None

        thread = threading.Thread(target=self._buffer_loop, name="rtsp_read_thread")
        thread.daemon = True
        thread.start()

    def stop(self):
        # Beendet _buffer_loop und gibt die Verbindung frei - noetig, wenn ein
        # Camera-Objekt durch ein neues ersetzt wird (z.B. Stand-Wechsel im
        # Ersteinrichtungs-Assistenten), sonst liefe der alte Hintergrund-Thread
        # als Daemon unbegrenzt weiter und versuchte endlos, die verworfene
        # (ggf. nicht erreichbare) alte URL erneut zu verbinden.
        self._stop_event.set()
        with self.lock:
            container = self._container
        # container.close() ausserhalb des Locks: unterbricht ein gerade
        # blockierendes next(frame_iter) im Hintergrund-Thread, damit stop()
        # nicht bis zum naechsten Frame/Reconnect-Versuch warten muss.
        if container is not None:
            try:
                container.close()
            except Exception:
                pass

    def _open_container(self):
        # options={"rtmp_live": "live"} bewusst weggelassen: fuehrt mit der auf
        # Stand 1 installierten ffmpeg-Version (4.3.9+rpt1) zu einem Segfault
        # in av.open() -- vermutlich ein Options-Dict-Bug in PyAV 10.0.0.
        container = av.open(self.rtsp_link)
        vstream = container.streams.video[0]
        # SLICE-Threading brachte im Vergleichstest den groessten CPU-Zeit-Gewinn
        # gegenueber cv2.VideoCapture (~10-13% weniger CPU-Zeit/Frame auf
        # echter Pi-4-Hardware)
        vstream.codec_context.thread_type = "SLICE"
        vstream.codec_context.thread_count = 4
        return container

    def _buffer_loop(self):
        container = None
        frame_iter = None
        while not self._stop_event.is_set():
            try:
                if container is None:
                    connect_started = time.time()
                    container = self._open_container()
                    with self.lock:
                        self._container = container
                    frame_iter = container.decode(video=0)
                    print(f"camera.py: Container geoeffnet nach {time.time() - connect_started:.1f}s", file=sys.stderr)
                av_frame = next(frame_iter)
                frame = av_frame.to_ndarray(format="bgr24")
            except (av.error.FFmpegError, StopIteration, OSError) as e:
                if self._stop_event.is_set():
                    # stop() hat den Container absichtlich geschlossen, um
                    # genau dieses next(frame_iter) zu unterbrechen - kein
                    # echter Verbindungsfehler, kein Retry noetig.
                    break
                # Typ+Meldung jedes einzelnen Fehlschlags loggen (via stderr,
                # landet ueber StandardError=journal des Service in
                # journald), sonst gibt es keinerlei Anhaltspunkt, ob/wie
                # oft/warum Verbindungsversuche scheitern - relevant u.a. fuer
                # die Fehlersuche bei Reboot-Guard-Fehlausloesungen.
                print(f"camera.py: Verbindungs-/Decode-Fehler ({type(e).__name__}: {e}), naechster Versuch in {self.reconnect_delay}s", file=sys.stderr)
                if container is not None:
                    container.close()
                container = None
                with self.lock:
                    self._container = None
                frame_iter = None
                self._stop_event.wait(self.reconnect_delay)
                continue

            full_frame = frame
            if self.crop_region is not None:
                x0, y0, x1, y1 = self.crop_region
                frame = frame[y0:min(y1, frame.shape[0]), x0:min(x1, frame.shape[1])]
            with self.lock:
                self.last_ready, self.last_frame = True, frame
                self.last_frame_full = full_frame
                self.frame_id += 1
                self.last_frame_time = time.time()

        # Regulaeres Schleifenende ueber die while-Bedingung (stop() kam
        # zwischen zwei Frames, nicht waehrend eines blockierenden next()) -
        # container ist dann meist schon durch stop() geschlossen, ein
        # zweiter close()-Versuch auf einem bereits geschlossenen Container
        # ist bei PyAV ungefaehrlich, daher hier ohne Sonderfall-Pruefung.
        if container is not None:
            try:
                container.close()
            except Exception:
                pass

    def getFrame(self, full=False):
        with self.lock:
            src = self.last_frame_full if full else self.last_frame
            if src is not None:
                return src.copy()
        return None

    def is_stale(self, timeout, startup_timeout=None):
        # kein neuer Frame seit mehr als "timeout" Sekunden - camera.py haengt
        # selbst bei dauerhaftem Verbindungsverlust nie (_buffer_loop faengt
        # alle Decode-/Verbindungsfehler ab und versucht endlos weiter), main.py
        # braucht diese Methode daher als eigenes Signal um irgendwann
        # aufzugeben und den Prozess zu beenden (siehe play_it/README)
        #
        # Vor dem allerersten Frame gilt ein eigener, grosszuegigerer
        # "startup_timeout" statt "timeout": av.open() hat keinen expliziten
        # Verbindungs-Timeout, ein frischer Verbindungsaufbau kann je nach
        # Netzwerk/Server-Zustand vereinzelt 30s+ dauern, obwohl
        # camera.py dabei keineswegs haengt - "timeout" ist dagegen bewusst
        # knapp bemessen fuer den Fall eines Ausfalls WAEHREND eines bereits
        # laufenden Streams. Ohne diese Unterscheidung wuerde ein einfach nur
        # etwas langsamer erster Verbindungsaufbau faelschlich als Ausfall
        # gewertet und main.py beendet, noch bevor ueberhaupt ein Frame
        # ankommen konnte.
        with self.lock:
            if self.frame_id == 0:
                grace = startup_timeout if startup_timeout is not None else timeout
                return (time.time() - self.start_time) > grace
            return (time.time() - self.last_frame_time) > timeout
