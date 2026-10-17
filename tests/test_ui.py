import pytest

import ui


class Cfg:
    def getProperty(self, key):
        return {'screenSize': [1280, 800]}[key]


@pytest.fixture
def win():
    ui.window = ui.Window(Cfg(), (780, 780), 'test')
    ui.window.show_page('-MAINVIEW-')
    ui.window.root.update()
    yield ui.window
    ui.window.close()
    ui.window = None


def test_sperren_und_entsperren_verschiebt_keine_buttons(win):
    # Beim Umschalten (Timer-Start, Replay, ...) bekommen alle Buttons neue
    # Farben. Dabei darf sich ihre Groesse nicht kurzzeitig aendern, sonst
    # springt das ganze Bedienfeld.
    changes = []
    for key, style in win._btn_style.items():
        outer = style['containers'][1]
        outer.bind('<Configure>', lambda e, key=key: changes.append((key, e.height)), add='+')
    for disable in (True, False, True, False):
        ui.zoom_disabled(disable)
        ui.sync_reset_button('full', [], globally_disabled=disable)
        ui.blink_disabled(disable)
        ui.timer_disabled(disable)
        ui.replay_buttons(not disable, disable)
        win.root.update()
    assert changes == []
