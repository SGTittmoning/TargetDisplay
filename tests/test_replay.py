import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
import pytest

import replay as r
import timerlib as t

FPS = 6


def frame(value=0, size=10):
    return np.full((size, size, 3), value, np.uint8)


def record(timer_type, until=None, latency=0.0, budget_bytes=10**9, size=10):
    rec = r.Recording(t.green_windows(timer_type), latency=latency, budget_bytes=budget_bytes)
    end = t.total_seconds(timer_type) if until is None else until
    n = 0
    while n / FPS < end:
        rec.add(frame(n % 256, size), n / FPS)
        n += 1
    return rec


def test_nur_gruenphasen_werden_aufgenommen():
    rec = record(t.TIMER_10)
    assert rec.frame_count() == 10 * FPS
    assert all(0 <= off < 10 for off, _ in rec.segments[0])


def test_serie_fuenf_durchgaenge_mit_trennbildern():
    rec = record(t.TIMER_5_3_7)
    assert [len(s) for s in rec.segments] == [3 * FPS] * 5
    assert rec.duration() == pytest.approx(5 * 3 + 4 * r.SEPARATOR_SEC)
    assert rec.view(0.0).kind == 'frame' and rec.view(0.0).segment == 0
    v = rec.view(3 + r.SEPARATOR_SEC / 2)
    assert v.kind == 'separator' and v.segment == 1
    v = rec.view(3 + r.SEPARATOR_SEC + 1.25)
    assert v.kind == 'frame' and v.segment == 1 and v.offset == pytest.approx(1.25)


def test_verzoegerung_verschiebt_das_aufnahmefenster():
    rec = r.Recording(t.green_windows(t.TIMER_10), latency=1.5)
    assert not rec.add(frame(), 7.2)    # kam vor Gruen + Verzoegerung an
    assert rec.add(frame(), 8.6)
    assert rec.add(frame(), 18.4)       # gruen bis 17, plus 1,5 s
    assert not rec.add(frame(), 18.6)
    assert rec.segments[0][0][0] == pytest.approx(0.1)


def test_verzoegerung_ist_begrenzt():
    assert r.Recording([(7, 10)], latency=9).latency == r.MAX_LATENCY_SEC
    assert r.Recording([(7, 10)], latency=-1).latency == 0.0


def test_is_complete():
    rec = r.Recording(t.green_windows(t.TIMER_10), latency=1.0)
    assert not rec.is_complete(17.5)
    assert rec.is_complete(18.0)


def test_speicherrahmen_halbiert_die_bildrate():
    one = frame(size=100).nbytes
    rec = record(t.TIMER_20, budget_bytes=50 * one, size=100)
    assert rec.bytes <= 50 * one
    assert rec.step >= 4
    offsets = [off for off, _ in rec.segments[0]]
    # Die Aufnahme deckt weiterhin die ganze Gruenphase ab.
    assert offsets[0] < 1 and offsets[-1] > 19


def test_bild_groesser_als_der_rahmen_wird_verworfen():
    rec = r.Recording([(0, 10)], latency=0, budget_bytes=10)
    assert not rec.add(frame(size=10), 1.0)
    assert not rec.has_frames()


def test_abbruch_mitten_im_durchgang():
    rec = record(t.TIMER_5_3_7, until=18.5)
    assert [len(s) for s in rec.segments if s] == [3 * FPS, 9]
    # zweiter Durchgang endet mit seinem letzten Bild
    assert rec.duration() == pytest.approx(3 + r.SEPARATOR_SEC + rec.segments[1][-1][0])


def test_nach_dem_ende_bleibt_das_letzte_bild():
    rec = record(t.TIMER_10)
    last = rec.view(rec.duration() + 5)
    assert last.frame is rec.segments[0][-1][1]
    assert last.offset == pytest.approx(10.0)


def test_bild_wechselt_nur_mit_neuem_kamerabild():
    rec = record(t.TIMER_10)
    a, b = rec.view(1.0), rec.view(1.1)
    assert a.key == b.key
    assert rec.view(1.2).key != a.key


def test_ohne_bilder_kein_replay():
    rec = r.Recording(t.green_windows(t.TIMER_10))
    assert not rec.has_frames()
    assert rec.view(0) is None
    assert rec.duration() == 0.0


def test_luecke_im_durchgang_liefert_kein_bild():
    rec = r.Recording(t.green_windows(t.TIMER_5_3_7), latency=0)
    rec.add(frame(), 8.0)       # Durchgang 1
    rec.add(frame(), 28.0)      # Durchgang 3, Durchgang 2 ohne Bild
    rec.add(frame(), 60.0)
    v = rec.view(3 + r.SEPARATOR_SEC + 1.0)
    assert v.segment == 1 and v.frame is None


def test_zaehler():
    v = r.ReplayView('frame', 1, 1.44, None, None)
    assert r.counter_text(v, 5) == 'Durchgang 2/5  1,4 s'
    assert r.counter_text(v._replace(offset=7.25), 1) == '7,2 s'
