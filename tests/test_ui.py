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


def posted_by_click(win, widget):
    # Klick wie mit dem Finger auf genau dieses Widget.
    widget.event_generate('<Button-1>')
    win.root.update()
    events = []
    while not win._queue.empty():
        events.append(win._queue.get_nowait()[0])
    return events


@pytest.mark.parametrize('part', ['frame', 'inner', 'label', 'button'])
def test_klick_auf_die_ganze_flaeche_loest_aus(win, part):
    style = win._btn_style['-TIMER_10-']
    widget = {'frame': style['containers'][1], 'inner': style['containers'][0],
              'label': style['label'], 'button': win['-TIMER_10-'].widget}[part]
    if part == 'button':
        widget.invoke()
        assert win._queue.get_nowait()[0] == '-TIMER_10-'
    else:
        assert posted_by_click(win, widget) == ['-TIMER_10-']


@pytest.mark.parametrize('lock, keys', [
    (lambda: ui.zoom_disabled(True), ('-FULL_VIDEO-', '-DETAIL_VIDEO-')),
    (lambda: ui.blink_disabled(True), ('-BLINK_START-', '-BLINK_REF-', '-BLINK_STOP-')),
    (lambda: ui.timer_disabled(True), ('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-', '-TIMER_STOP-')),
    (lambda: ui.replay_buttons(False, False), ('-REPLAY-',)),
    (lambda: ui.video_filter_disabled(True), ('-TOGGLEVIDEO-',)),
])
def test_gesperrte_buttons_loesen_nichts_aus(win, lock, keys):
    lock()
    for key in keys:
        win[key].widget.invoke()
        if key in win._btn_style:
            posted_by_click(win, win._btn_style[key]['containers'][1])
    assert win._queue.empty()


def test_replay_knopf_wechselt_zu_replay_ende(win):
    ui.replay_buttons(True, True)
    win['-REPLAY-'].widget.invoke()
    assert win._queue.get_nowait()[0] == '-REPLAY_END-'
    assert win._btn_style['-REPLAY-']['label'].cget('text') == 'Replay Ende'
    ui.replay_buttons(True, False)
    win['-REPLAY-'].widget.invoke()
    assert win._queue.get_nowait()[0] == '-REPLAY-'


def test_langer_standname_wird_gekuerzt(win):
    win.set_stand_name('Stand mit einem sehr, sehr langen Namen für die Kopfzeile')
    text = win['-STANDNAME-'].widget.cget('text')
    assert text.endswith('…')
    assert win._standname_font.measure(text) <= ui.STANDNAME_MAX_PX


def test_kurzer_standname_bleibt(win):
    win.set_stand_name('Stand 1')
    assert win['-STANDNAME-'].widget.cget('text') == 'Stand 1'
