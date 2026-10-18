import time

import numpy as np

import camera

# Jedes Pixel traegt seine Koordinaten (Kanal 0: x, Kanal 1: y), damit ein
# Zuschnitt mit vertauschten Achsen auffaellt.
FRAME_H, FRAME_W = 12, 20
_y, _x = np.mgrid[0:FRAME_H, 0:FRAME_W]
FRAME = np.dstack([_x, _y, np.zeros_like(_x)]).astype(np.uint8)


class FakeFrame:
    def to_ndarray(self, format):
        return FRAME.copy()


class FakeContainer:
    def __init__(self, frames, error=None, close_error=None):
        self.frames = frames
        self.error = error
        self.close_error = close_error

    def decode(self, video):
        for _ in range(self.frames):
            time.sleep(0.005)
            yield FakeFrame()
        if self.error is not None:
            raise self.error
        while True:
            time.sleep(0.005)
            yield FakeFrame()

    def close(self):
        if self.close_error is not None:
            raise self.close_error


def start_camera(monkeypatch, containers, reconnect_delay=0.01):
    it = iter(containers)
    monkeypatch.setattr(camera.Camera, '_open_container', lambda self: next(it))
    return camera.Camera('rtmp://test.invalid/live', reconnect_delay=reconnect_delay)


def wait_for_frames(cam, count, timeout=3.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cam.snapshot()[0] >= count:
            return True
        time.sleep(0.01)
    return False


def test_liefert_bilder_und_zuschnitt(monkeypatch):
    cam = start_camera(monkeypatch, [FakeContainer(0)])
    # Nicht quadratisch, nicht am Ursprung: (x0, y0, x1, y1).
    cam.set_crop_region((3, 2, 15, 7))
    try:
        assert wait_for_frames(cam, 3)
        assert np.array_equal(cam.get_frame(), FRAME[2:7, 3:15])
        assert np.array_equal(cam.get_frame(full=True), FRAME)
    finally:
        cam.stop()


def test_zuschnitt_ueber_den_bildrand_wird_begrenzt(monkeypatch):
    cam = start_camera(monkeypatch, [FakeContainer(0)])
    cam.set_crop_region((10, 5, 40, 30))
    try:
        assert wait_for_frames(cam, 3)
        assert np.array_equal(cam.get_frame(), FRAME[5:, 10:])
    finally:
        cam.stop()


def test_verbindet_nach_bekanntem_fehler_neu(monkeypatch):
    cam = start_camera(monkeypatch, [FakeContainer(2, error=OSError('weg')), FakeContainer(0)])
    try:
        assert wait_for_frames(cam, 10)
    finally:
        cam.stop()


def test_unerwartete_ausnahme_beendet_den_lesethread_nicht(monkeypatch, capsys):
    cam = start_camera(monkeypatch, [FakeContainer(2, error=ValueError('kaputt')), FakeContainer(0)])
    try:
        assert wait_for_frames(cam, 10)
    finally:
        cam.stop()
    assert 'ValueError' in capsys.readouterr().err


def test_fehler_beim_schliessen_beendet_den_lesethread_nicht(monkeypatch):
    first = FakeContainer(2, error=OSError('weg'), close_error=RuntimeError('close'))
    cam = start_camera(monkeypatch, [first, FakeContainer(0)])
    try:
        assert wait_for_frames(cam, 10)
    finally:
        cam.stop()


def test_zugangsdaten_landen_nicht_im_log(monkeypatch, capsys):
    error = OSError('rtmp://admin:geheim@cam/live nicht erreichbar')
    cam = start_camera(monkeypatch, [FakeContainer(1, error=error), FakeContainer(0)])
    try:
        assert wait_for_frames(cam, 5)
    finally:
        cam.stop()
    assert 'geheim' not in capsys.readouterr().err


def test_stop_beendet_den_lesethread(monkeypatch):
    cam = start_camera(monkeypatch, [FakeContainer(0)])
    assert wait_for_frames(cam, 3)
    cam.stop()
    cam._thread.join(timeout=2)
    assert not cam._thread.is_alive()


def test_stop_waehrend_der_wartezeit_vor_dem_neuen_versuch(monkeypatch):
    cam = start_camera(monkeypatch, [FakeContainer(1, error=OSError('weg')), FakeContainer(0)],
                       reconnect_delay=30)
    assert wait_for_frames(cam, 1)
    time.sleep(0.1)
    cam.stop()
    cam._thread.join(timeout=2)
    assert not cam._thread.is_alive()


def write_video(path, frames=30, size=(64, 48)):
    """Kleines Video mit PyAV: jedes Bild links schwarz, rechts weiss."""
    import av
    with av.open(str(path), 'w') as out:
        stream = out.add_stream('mpeg4', rate=10)
        stream.width, stream.height = size
        stream.pix_fmt = 'yuv420p'
        img = np.zeros((size[1], size[0], 3), np.uint8)
        img[:, size[0] // 2:] = 255
        for _ in range(frames):
            for packet in stream.encode(av.VideoFrame.from_ndarray(img, format='bgr24')):
                out.mux(packet)
        for packet in stream.encode():
            out.mux(packet)


def test_echte_dekodierung_mit_pyav(tmp_path):
    # Der echte Weg ueber open_container und to_ndarray (bgr24), mit Zuschnitt.
    path = tmp_path / 'scheibe.mp4'
    write_video(path)
    cam = camera.Camera(str(path), reconnect_delay=0.01)
    cam.set_crop_region((28, 10, 40, 30))
    try:
        assert wait_for_frames(cam, 3)
        full, crop = cam.get_frame(full=True), cam.get_frame()
    finally:
        cam.stop()
    assert full.shape == (48, 64, 3) and crop.shape == (20, 12, 3)
    # Links dunkel, rechts hell (Kompressionsrauschen zugelassen).
    assert full[:, :20].mean() < 40 and full[:, 44:].mean() > 215
    assert crop[:, :2].mean() < 60 and crop[:, -2:].mean() > 195
