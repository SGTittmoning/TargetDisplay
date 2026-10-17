import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture
def app(tmp_path, monkeypatch):
    # Die App mit der Standard-Testkonfiguration (siehe app_harness).
    from app_harness import CONFIG, load_app
    return load_app(tmp_path, monkeypatch, CONFIG)
