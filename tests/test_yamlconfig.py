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
    assert cfg.get_property('video.url') == 'rtmp://example/live'
    assert cfg.get_property('video.section_full') == [[0, 0], [1, 1]]


def test_flacher_pfad(cfg):
    assert cfg.get_property('standName') == 'Stand 1'
    assert cfg.get_property('screenSize') == [1024, 768]


def test_fehlender_pfad_wirft_keyerror(cfg):
    with pytest.raises(KeyError):
        cfg.get_property('video.section_detail')
    with pytest.raises(KeyError):
        cfg.get_property('nicht_vorhanden')


def test_default_bei_fehlendem_pfad(cfg):
    assert cfg.get_property_with_default('video.section_detail', None) is None
    assert cfg.get_property_with_default('settingsPin', '1234') == '1234'


def test_default_wird_bei_vorhandenem_wert_nicht_genutzt(cfg):
    assert cfg.get_property_with_default('standName', 'Fallback') == 'Stand 1'


def test_default_bei_kaputter_zwischenebene(tmp_path):
    # "video: null" ist gueltiges YAML - node['video'] liefert erfolgreich
    # None, der naechste Traversierungsschritt None['section_full'] wirft
    # dann TypeError statt KeyError.
    p = tmp_path / 'config.yml'
    p.write_text("video: null\n")
    cfg = yamlconfig.load(str(p))
    assert cfg.get_property_with_default('video.section_full', None) is None
    with pytest.raises(TypeError):
        cfg.get_property('video.section_full')


def test_default_bei_skalarer_zwischenebene(tmp_path):
    p = tmp_path / 'config.yml'
    p.write_text("video: 'nicht_verschachtelt'\n")
    cfg = yamlconfig.load(str(p))
    assert cfg.get_property_with_default('video.section_full', 'X') == 'X'


def test_leere_datei(tmp_path):
    p = tmp_path / 'config.yml'
    p.write_text('')
    assert yamlconfig.load(str(p)).get_property_with_default('irgendwas', 'default') == 'default'
