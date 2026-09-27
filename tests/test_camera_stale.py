import time

import camera


def make_camera(monkeypatch, now):
    # Camera ohne Hintergrund-Thread: nur die Staleness-Logik wird geprueft.
    clock = {'now': now}
    monkeypatch.setattr(time, 'monotonic', lambda: clock['now'])
    monkeypatch.setattr(camera.threading.Thread, 'start', lambda self: None)
    cam = camera.Camera('rtmp://example.invalid/live/x')
    return cam, clock


def test_startup_frist_gilt_vor_dem_ersten_frame(monkeypatch):
    cam, clock = make_camera(monkeypatch, 1000.0)
    clock['now'] = 1025.0
    assert not cam.is_stale(10, startup_timeout=30)
    clock['now'] = 1031.0
    assert cam.is_stale(10, startup_timeout=30)


def test_stale_nach_ausbleibendem_frame(monkeypatch):
    cam, clock = make_camera(monkeypatch, 1000.0)
    cam.frame_id = 5
    cam.last_frame_time = 1000.0
    clock['now'] = 1009.0
    assert not cam.is_stale(10, startup_timeout=30)
    clock['now'] = 1011.0
    assert cam.is_stale(10, startup_timeout=30)


def test_snapshot_liefert_frame_id_last_frame_time_start_time(monkeypatch):
    cam, clock = make_camera(monkeypatch, 1000.0)
    cam.frame_id = 5
    cam.last_frame_time = 1004.0
    assert cam.snapshot() == (5, 1004.0, 1000.0)


def test_set_crop_region(monkeypatch):
    cam, _ = make_camera(monkeypatch, 1000.0)
    assert cam.crop_region is None
    cam.set_crop_region((1, 2, 3, 4))
    assert cam.crop_region == (1, 2, 3, 4)


def test_zeitsprung_der_systemuhr_loest_kein_stale_aus(monkeypatch):
    # Die Systemuhr (time.time) springt um Wochen vor, z.B. bei der ersten
    # NTP-Synchronisation nach dem Boot - die monotone Uhr bleibt davon
    # unberuehrt.
    cam, clock = make_camera(monkeypatch, 1000.0)
    cam.frame_id = 5
    cam.last_frame_time = 1000.0
    clock['now'] = 1003.0
    monkeypatch.setattr(time, 'time', lambda: 1000.0 + 18 * 86400)
    assert not cam.is_stale(10, startup_timeout=30)
