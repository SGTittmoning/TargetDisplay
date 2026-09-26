import json

import pytest

import settings_store as store
import transformlib as tl


class FakeCfg:
    def __init__(self, values=None):
        self.values = values or {}

    def getPropertyWithDefault(self, key, default):
        return self.values.get(key, default)


@pytest.fixture
def files(tmp_path, monkeypatch):
    paths = {
        'SECTIONS_OVERRIDE_FILE': tmp_path / 'sections.json',
        'STANDS_FILE': tmp_path / 'stands.json',
        'ACTIVE_STAND_FILE': tmp_path / 'active.json',
        'PIN_FILE': tmp_path / 'pin.json',
    }
    for attr, path in paths.items():
        monkeypatch.setattr(store, attr, str(path))
    return paths


STANDS = {'stands': [
    {'id': 'stand1', 'displayName': 'Stand 1', 'url': 'rtmp://a'},
    {'id': 'stand2', 'displayName': 'Stand 2', 'url': 'rtmp://b'},
    {'id': 'kaputt', 'displayName': '', 'url': 'rtmp://c'},
]}


def test_load_stands_ohne_datei_ist_leer(files):
    assert store.load_stands() == []


def test_load_stands_filtert_unvollstaendige_eintraege(files):
    files['STANDS_FILE'].write_text(json.dumps(STANDS))
    assert [s['id'] for s in store.load_stands()] == ['stand1', 'stand2']


def test_load_stands_mit_kaputter_datei(files):
    files['STANDS_FILE'].write_text('{nicht json')
    assert store.load_stands() == []


def test_active_stand_aus_datei(files):
    files['STANDS_FILE'].write_text(json.dumps(STANDS))
    files['ACTIVE_STAND_FILE'].write_text('{"id": "stand2"}')
    stands = store.load_stands()
    assert store.load_active_stand(stands, FakeCfg())['url'] == 'rtmp://b'


def test_active_stand_fallback_auf_config(files):
    cfg = FakeCfg({'video.url': 'rtmp://cfg', 'standName': 'Einzelstand'})
    assert store.load_active_stand([], cfg) == {'id': None, 'displayName': 'Einzelstand', 'url': 'rtmp://cfg'}


def test_active_stand_ohne_auswahl_und_ohne_config(files):
    assert store.load_active_stand([], FakeCfg()) is None


def test_active_stand_unbekannte_id_gilt_als_nicht_ausgewaehlt(files):
    files['STANDS_FILE'].write_text(json.dumps(STANDS))
    files['ACTIVE_STAND_FILE'].write_text('{"id": "gibt-es-nicht"}')
    assert store.load_active_stand(store.load_stands(), FakeCfg()) is None


def test_sections_override_hat_vorrang(files):
    cfg = FakeCfg({'video.section_full': [[0, 0]], 'video.section_detail': [[1, 1]]})
    files['SECTIONS_OVERRIDE_FILE'].write_text('{"section_full": [[5, 5]]}')
    assert store.load_sections(cfg) == ([[5, 5]], [[1, 1]])


def test_sections_ohne_datei_und_config(files):
    assert store.load_sections(FakeCfg()) == (None, None)


def test_settings_pin_prioritaet(files):
    assert store.load_settings_pin(FakeCfg()) == store.DEFAULT_PIN
    assert store.load_settings_pin(FakeCfg({'settingsPin': '4711'})) == '4711'
    files['PIN_FILE'].write_text('{"pin": "9999"}')
    assert store.load_settings_pin(FakeCfg({'settingsPin': '4711'})) == '9999'


def test_save_pin_ruft_das_sudo_skript_mit_json(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stderr = ''

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs['input']))
        return Result()

    monkeypatch.setattr(store.subprocess, 'run', fake_run)
    assert store.save_pin('4711')
    cmd, payload = calls[0]
    assert cmd == ['/usr/bin/sudo', '/root/bin/targetdisplay-save-pin.sh']
    assert json.loads(payload) == {'pin': '4711'}


def test_save_schlaegt_fehl_bei_exitcode(monkeypatch):
    class Result:
        returncode = 1
        stderr = 'kaputt'

    monkeypatch.setattr(store.subprocess, 'run', lambda *a, **k: Result())
    assert store.save_active_stand('stand1') is False


def test_clamp_zoom_center():
    assert tl.clamp_zoom_center((0, 100)) == (100 / 6, 100 - 100 / 6)
    assert tl.clamp_zoom_center((50, 50)) == (50, 50)
