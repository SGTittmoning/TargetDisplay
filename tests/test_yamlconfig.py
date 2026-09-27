import pytest

import yamlconfig


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / 'config.yml'
    p.write_text("""
standName: Stand 1
screenSize: [1024, 768]
video:
  url: 'rtmp://example/live'
  section_full: [[0, 0], [1, 1]]
""")
    return yamlconfig.load(str(p))


def test_verschachtelter_pfad(cfg):
    assert cfg.getProperty('video.url') == 'rtmp://example/live'
    assert cfg.getProperty('video.section_full') == [[0, 0], [1, 1]]


def test_flacher_pfad(cfg):
    assert cfg.getProperty('standName') == 'Stand 1'
    assert cfg.getProperty('screenSize') == [1024, 768]


def test_fehlender_pfad_wirft_keyerror(cfg):
    with pytest.raises(KeyError):
        cfg.getProperty('video.section_detail')
    with pytest.raises(KeyError):
        cfg.getProperty('nicht_vorhanden')


def test_default_bei_fehlendem_pfad(cfg):
    assert cfg.getPropertyWithDefault('video.section_detail', None) is None
    assert cfg.getPropertyWithDefault('settingsPin', '1234') == '1234'


def test_default_wird_bei_vorhandenem_wert_nicht_genutzt(cfg):
    assert cfg.getPropertyWithDefault('standName', 'Fallback') == 'Stand 1'


def test_leere_datei():
    import tempfile, os
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        c = yamlconfig.load(path)
        assert c.getPropertyWithDefault('irgendwas', 'default') == 'default'
    finally:
        os.remove(path)
