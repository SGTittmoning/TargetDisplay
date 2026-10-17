"""Rechenteile von tools/latenz_messung.py mit kuenstlichen Bildern."""
import importlib.util
import os

import numpy as np
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope='module')
def lm():
    spec = importlib.util.spec_from_file_location('latenz_messung', os.path.join(ROOT, 'tools', 'latenz_messung.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def clock_bands(lm, fps=6, noise=0):
    """Bildstreifen mit einer Uhr: die Sekunden-Einerstelle (x 200-220)
    wechselt jede Sekunde, die Zehnerstelle (x 170-190) einmal."""
    rng = np.random.default_rng(1)
    bands = []
    for i in range(lm.CALIB_SEC * fps):
        band = np.full((60, 400), 30, np.uint8)
        second = i // fps
        band[10:40, 200:220] = 255 if second % 2 else 0
        band[10:40, 170:190] = 255 if second >= 7 else 0
        if noise:
            band = np.clip(band.astype(int) + rng.integers(-noise, noise + 1, band.shape), 0, 255).astype(np.uint8)
        bands.append(band)
    return bands


def test_findet_die_sekundenziffer(lm):
    x0, y0, x1, y1 = lm.find_seconds_box(clock_bands(lm))
    # Rahmen um die Einerstelle (x 200-220, y 10-40) mit wenigen Pixeln Rand,
    # die Zehnerstelle (bis x 190) liegt ausserhalb.
    assert 190 < x0 <= 200 and 220 <= x1 < 230
    assert 0 < y0 <= 10 and 40 <= y1 < 50


def test_rauschen_stoert_nicht(lm):
    x0, _, x1, _ = lm.find_seconds_box(clock_bands(lm, noise=15))
    assert 190 <= x0 <= 200 and 220 <= x1 <= 230


def test_ohne_uhr_kein_ergebnis(lm):
    bands = [np.full((60, 400), 30, np.uint8) for _ in range(lm.CALIB_SEC * 6)]
    assert lm.find_seconds_box(bands) is None


def test_kreismittel_ueber_die_sekundengrenze(lm):
    # 0,95 und 0,05 liegen 0,1 s auseinander, ihr Mittel ist 0,0 und nicht 0,5.
    mean, spread = lm.circular_mean([0.95, 0.05, 0.97, 0.03])
    assert min(mean, 1 - mean) < 0.01
    assert spread < 0.1


def test_kreismittel_und_streuung(lm):
    mean, spread = lm.circular_mean([0.30, 0.32, 0.34, 0.36, 0.38])
    assert mean == pytest.approx(0.34, abs=0.005)
    assert 0 < spread < 0.05
