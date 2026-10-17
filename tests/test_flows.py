# Dialogablaeufe aus flows.py ohne Hauptschleife: Ereignisse werden vorab
# (oder aus einer Fake-Kamera heraus) in die Warteschlange gelegt,
# Window.read() liest sie vor allen Tk-Ereignissen.
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
