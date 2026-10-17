"""Die config.yml, die Ansible aus ansible/templates/config.yml.j2 erzeugt,
muss sich von der App lesen lassen."""
import os

import pytest
import yaml

import replay
import settings_store
import yamlconfig

jinja2 = pytest.importorskip('jinja2')

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.join(ROOT, 'ansible')


@pytest.fixture(autouse=True)
def no_pin_file(monkeypatch, tmp_path):
    monkeypatch.setattr(settings_store, 'PIN_FILE', str(tmp_path / 'fehlt.json'))


def playbook_vars(name):
    with open(os.path.join(ANSIBLE, name), encoding='utf-8') as f:
        return yaml.safe_load(f)[0]['vars']


def render(**variables):
    with open(os.path.join(ANSIBLE, 'templates', 'config.yml.j2'), encoding='utf-8') as f:
        template = jinja2.Template(f.read(), undefined=jinja2.StrictUndefined)
    defaults = {k: v for k, v in playbook_vars('install.yml').items() if k.startswith('targetdisplay_screen_')}
    text = template.render(**{**defaults, **variables})
    return yamlconfig.Config(yaml.safe_load(text))


def test_bildschirmgroesse_in_beiden_playbooks_gleich():
    # update-app.yml erzeugt config.yml ebenfalls; abweichende Werte wuerden
    # bei jedem Update die Groesse aendern.
    keys = ('targetdisplay_screen_width', 'targetdisplay_screen_height')
    install, update = playbook_vars('install.yml'), playbook_vars('update-app.yml')
    assert [install[k] for k in keys] == [update[k] for k in keys]


def test_standardwerte(capsys):
    cfg = render()
    assert cfg.get_property('screenSize') == [1280, 800]
    assert (cfg.get_property('video.size.x'), cfg.get_property('video.size.y')) == (780, 780)
    # Ohne my_settings_pin gilt der Standard-PIN, der Assistent erzwingt die Aenderung.
    assert settings_store.load_settings_pin(cfg) == settings_store.DEFAULT_PIN
    assert replay.read_settings(cfg) == (replay.DEFAULT_LATENCY_SEC, replay.DEFAULT_BUDGET_MB * 1024 * 1024)
    assert 'ungültig' not in capsys.readouterr().err


def test_gesetzte_werte():
    cfg = render(my_settings_pin='0815', targetdisplay_replay_latency_sec=1.4,
                 targetdisplay_replay_budget_mb=300)
    # Fuehrende Null bleibt erhalten (in der Vorlage in Anfuehrungszeichen).
    assert settings_store.load_settings_pin(cfg) == '0815'
    assert replay.read_settings(cfg) == (1.4, 300 * 1024 * 1024)


def test_keine_stand_daten_in_der_vorlage():
    # Kamera-URL, Ausschnitte und Standname kommen aus der Stand-Liste und
    # dem Assistenten, nicht aus config.yml.
    cfg = render()
    for key in ('video.url', 'video.section_full', 'video.section_detail', 'standName'):
        assert cfg.get_property_with_default(key, None) is None
