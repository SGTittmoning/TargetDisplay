import gc
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Die App mit der Standard-Testkonfiguration (siehe app_harness).
    from app_harness import CONFIG, load_app
    return load_app(tmp_path, monkeypatch, CONFIG)


@pytest.fixture(autouse=True)
def tk_objects_freed_in_main_thread():
    # Tk-Objekte (Bilder, Interpreter) frueherer Tests muessen im Haupt-
    # Thread freigegeben werden. Raeumt der Garbage Collector sie zufaellig
    # in einem Hintergrund-Thread (Treiber der Oberflaechen-Tests) ab,
    # stoert das den laufenden Tk-Interpreter.
    gc.collect()
    yield
    gc.collect()
