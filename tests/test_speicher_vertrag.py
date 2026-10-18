"""Speichern ueber sudo: Python, Speicher-Skripte, sudo-Freigabe und Ansible
muessen dieselben Datei- und Skriptnamen verwenden. Ein Tippfehler an einer
Stelle faellt sonst erst am Geraet auf (Einstellung nach dem Neustart weg)."""
import os
import re
import subprocess

import pytest
import yaml

import flows
import settings_store

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANSIBLE = os.path.join(ROOT, 'ansible')

# Speicherfunktion, Beispielwerte und die Datei, aus der die App den Wert
# wieder liest.
SAVES = [
    (settings_store.save_sections_override, ([[0, 0]] * 4, [[1, 1]] * 4), 'SECTIONS_OVERRIDE_FILE'),
    (settings_store.save_active_stand, ('stand1',), 'ACTIVE_STAND_FILE'),
    (settings_store.save_pin, ('2580',), 'PIN_FILE'),
]


def called_script(monkeypatch, save, args):
    calls = []
    monkeypatch.setattr(settings_store.subprocess, 'run',
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert save(*args) is True
    [cmd] = calls
    assert cmd[0] == '/usr/bin/sudo'
    return cmd[1]


def read(*parts):
    with open(os.path.join(ROOT, *parts), encoding='utf-8') as f:
        return f.read()


def sudoers_commands():
    [task] = yaml.safe_load(read('ansible', 'tasks', 'settings_sudo.yml'))
    return set(re.findall(r'NOPASSWD: (\S+)', task['copy']['content']))


def deployed_scripts():
    tasks = yaml.safe_load(read('ansible', 'tasks', 'maintenance_scripts.yml'))
    return {item for task in tasks for item in task.get('loop', [])}


@pytest.mark.parametrize('save, args, file_attr', SAVES, ids=[s[2] for s in SAVES])
def test_skript_datei_und_freigabe_passen_zusammen(monkeypatch, save, args, file_attr):
    script_path = called_script(monkeypatch, save, args)
    script = os.path.basename(script_path)
    assert script_path == f'/root/bin/{script}'
    # Das Skript liegt im Repo, wird verteilt und ist per sudo freigegeben.
    assert os.path.isfile(os.path.join(ANSIBLE, 'files', script))
    assert script in deployed_scripts()
    assert script_path in sudoers_commands()
    # Es schreibt genau die Datei, die die App beim Start liest.
    target = re.search(r'^save_boot_json "([^"]+)"', read('ansible', 'files', script), re.M).group(1)
    boot_file = getattr(settings_store, file_attr)
    assert f'/boot/firmware/{target}' == boot_file


def test_vorbelegung_nutzt_dieselben_dateien_und_skripte():
    [play] = yaml.safe_load(read('ansible', 'boot-overrides.yml'))
    by_file = {o['file']: o['script'] for o in play['vars']['overrides']}
    assert by_file == {
        os.path.basename(settings_store.ACTIVE_STAND_FILE): 'targetdisplay-save-active-stand.sh',
        os.path.basename(settings_store.SECTIONS_OVERRIDE_FILE): 'targetdisplay-save-sections.sh',
    }


def test_neustart_ist_freigegeben(monkeypatch):
    calls = []
    monkeypatch.setattr(flows.subprocess, 'run',
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    assert flows._reboot() is True
    [cmd] = calls
    assert cmd[0] == '/usr/bin/sudo' and cmd[1] in sudoers_commands()
