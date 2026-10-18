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


def load(*parts):
    with open(os.path.join(ANSIBLE, *parts), encoding='utf-8') as f:
        return yaml.safe_load(f)


def render(**variables):
    with open(os.path.join(ANSIBLE, 'templates', 'config.yml.j2'), encoding='utf-8') as f:
        template = jinja2.Template(f.read(), undefined=jinja2.StrictUndefined)
    defaults = {k: v for k, v in load('vars', 'common.yml').items() if k.startswith('targetdisplay_screen_')}
    text = template.render(**{**defaults, **variables})
    return yamlconfig.Config(yaml.safe_load(text))


def test_beide_playbooks_nutzen_die_gemeinsamen_werte():
    # update-app.yml erzeugt config.yml ebenfalls; eigene Werte dort wuerden
    # bei jedem Update etwas anderes einrichten.
    common = load('vars', 'common.yml')
    for name in ('install.yml', 'update-app.yml'):
        [play] = load(name)
        assert play['vars_files'] == ['vars/common.yml']
        assert not set(play['vars']) & set(common)


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


def render_stands(**variables):
    import json
    env = jinja2.Environment(undefined=jinja2.StrictUndefined)
    env.filters['to_json'] = json.dumps
    with open(os.path.join(ANSIBLE, 'templates', 'stands.json.j2'), encoding='utf-8') as f:
        return json.loads(env.from_string(f.read()).render(**variables))


STANDS = [{'id': 'stand1', 'displayName': 'Stand "1"', 'url': 'rtmp://a/b'},
          {'id': 'stand2', 'displayName': 'Stand 2', 'url': 'rtmp://c/d'}]


def test_stand_liste_wird_gueltiges_json():
    # Auch Anfuehrungszeichen im Namen bleiben gueltiges JSON.
    assert render_stands(targetdisplay_stands=STANDS) == {'stands': STANDS}


def test_zusatzstaende_eines_hosts_kommen_hinten_dazu():
    extra = [{'id': 'teststand', 'displayName': 'Teststand', 'url': 'rtmp://e/f'}]
    stands = render_stands(targetdisplay_stands=STANDS, targetdisplay_extra_stands=extra)['stands']
    assert [s['id'] for s in stands] == ['stand1', 'stand2', 'teststand']


def test_gerenderte_liste_liest_die_app(tmp_path, monkeypatch):
    import json
    path = tmp_path / 'stands.json'
    path.write_text(json.dumps(render_stands(targetdisplay_stands=STANDS)))
    monkeypatch.setattr(settings_store, 'STANDS_FILE', str(path))
    assert settings_store.load_stands() == STANDS


def test_release_tag_passt_zur_version():
    import main
    assert load('vars', 'common.yml')['targetdisplay_release'] == 'v' + main.VERSION


def test_app_deploy_nutzt_das_release_tag():
    [task] = [t for t in load('tasks', 'app_deploy.yml') if 'git' in t]
    assert task['git']['version'] == "{{ targetdisplay_git_version | default(targetdisplay_release) }}"
