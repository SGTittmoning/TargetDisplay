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

        thread = threading.Thread(target=self._buffer_loop, name="rtsp_read_thread")
        thread.daemon = True
        thread.start()

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
        while True:
            try:
                if container is None:
                    connect_started = time.time()
                    container = self._open_container()
                    frame_iter = container.decode(video=0)
                    print(f"camera.py: Container geoeffnet nach {time.time() - connect_started:.1f}s", file=sys.stderr)
                av_frame = next(frame_iter)
                frame = av_frame.to_ndarray(format="bgr24")
            except (av.error.FFmpegError, StopIteration, OSError) as e:
                # Bisher wurde hier stillschweigend weiterversucht, ohne
                # jemals zu protokollieren WAS eigentlich schiefging - bei der
                # Fehlersuche zu Reboot-Guard-Fehlausloesungen (2026-08-28)
                # gab es dadurch keinerlei Anhaltspunkt, ob/wie oft/warum
                # Verbindungsversuche scheitern. Deshalb jetzt Typ+Meldung
                # jedes einzelnen Fehlschlags loggen (via stderr, landet ueber
                # StandardError=journal des Service in journald).
                print(f"camera.py: Verbindungs-/Decode-Fehler ({type(e).__name__}: {e}), naechster Versuch in {self.reconnect_delay}s", file=sys.stderr)
                if container is not None:
                    container.close()
                container = None
                frame_iter = None
                time.sleep(self.reconnect_delay)
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
        # Netzwerk/Server-Zustand vereinzelt 30s+ dauern (beobachtet), obwohl
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
