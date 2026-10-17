# Dialogablaeufe aus flows.py ohne Hauptschleife: Ereignisse werden vorab
# (oder aus einer Fake-Kamera heraus) in die Warteschlange gelegt,
# Window.read() liest sie vor allen Tk-Ereignissen.
import subprocess

import numpy as np
import pytest

import flows
import ui


class Cfg:
    def getProperty(self, key):
        return {'screenSize': [1024, 768]}[key]


@pytest.fixture
def win():
    ui.window = ui.Window(Cfg(), (600, 600), 'test')
    yield ui.window
    ui.window.close()
    ui.window = None


def camwait_text():
    return ui.window['-CAMWAIT_TEXT-'].widget.cget('text')


def test_kamera_warten_erneut_versuchen_startet_die_wartezeit_neu(win):
    # Ablauf: Wartezeit laeuft ab -> "Erneut versuchen" -> wieder
    # "Verbinde mit Kamera..." -> erst dann kommt ein Bild.
    texts = []

    class Cam:
        def getFrame(self, full=False):
            texts.append(camwait_text())
            if texts[-1].startswith('Kamera nicht erreichbar'):
                win.post('-CAMWAIT_RETRY-')
            retried = any(t.startswith('Kamera nicht erreichbar') for t in texts[:-1])
            if (retried and texts[-1].startswith('Verbinde')) or len(texts) > 15:
                return np.zeros((4, 4, 3), np.uint8)
            return None

    assert flows.wait_for_camera_frame(Cam(), max_wait_sec=0.2) is not None
    assert texts[-1].startswith('Verbinde'), texts


def test_kamera_warten_zurueck(win):
    class Cam:
        def getFrame(self, full=False):
            return None

    win.post('-CAMWAIT_BACK-')
    assert flows.wait_for_camera_frame(Cam(), max_wait_sec=5) == 'back'


@pytest.mark.parametrize('outcome', [1, OSError('sudo fehlt')])
def test_fehlgeschlagener_neustart_meldet_sich(win, monkeypatch, outcome):
    messages = []
    monkeypatch.setattr(flows, 'popup', messages.append)

    def fake_run(cmd, **kwargs):
        if isinstance(outcome, Exception):
            raise outcome
        return subprocess.CompletedProcess(cmd, outcome)

    monkeypatch.setattr(flows.subprocess, 'run', fake_run)
    for event in ('-MENU_RESTART-', '-CONFIRM_YES-', '-MENU_BACK-'):
        win.post(event)
    assert flows.run_settings_flow(None, None, None, [], '4711') is False
    assert messages == ['Neustart fehlgeschlagen.']


def test_neustart_ohne_fehler_ohne_meldung(win, monkeypatch):
    messages, calls = [], []
    monkeypatch.setattr(flows, 'popup', messages.append)
    monkeypatch.setattr(flows.subprocess, 'run',
                        lambda cmd, **kw: calls.append(cmd) or subprocess.CompletedProcess(cmd, 0))
    for event in ('-MENU_RESTART-', '-CONFIRM_YES-', '-MENU_BACK-'):
        win.post(event)
    flows.run_settings_flow(None, None, None, [], '4711')
    assert calls == [['/usr/bin/sudo', '/usr/sbin/reboot']]
    assert messages == []
