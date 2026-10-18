import pytest
from app_harness import needs_display, open_window

import ui

pytestmark = needs_display


@pytest.fixture
def win():
    with open_window(ui, [1280, 800], (780, 780)) as window:
        window.show_page('-MAINVIEW-')
        yield window


def test_sperren_und_entsperren_verschiebt_keine_buttons(win):
    # Beim Umschalten (Timer-Start, Replay, ...) bekommen alle Buttons neue
    # Farben. Dabei darf sich ihre Groesse nicht kurzzeitig aendern, sonst
    # springt das ganze Bedienfeld.
    changes = []
    for key, style in win._btn_style.items():
        outer = style['containers'][1]
        outer.bind('<Configure>', lambda e, key=key: changes.append((key, e.height)), add='+')
    for mode in ('timer', 'live', 'replay', 'blank', 'live'):
        ui.apply_controls(mode, has_recording=True)
        win.root.update()
    assert changes == []


def drain(win):
    events = []
    while not win._queue.empty():
        events.append(win._queue.get_nowait()[0])
    return events


def posted_by_click(win, widget):
    # Klick wie mit dem Finger auf genau dieses Widget.
    widget.event_generate('<Button-1>')
    win.root.update()
    return drain(win)


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


ALL = ('-FULL_VIDEO-', '-DETAIL_VIDEO-', '-RESETZOOM-', '-BLINK_START-', '-BLINK_REF-',
       '-BLINK_STOP-', '-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-', '-TIMER_STOP-',
       '-REPLAY-', '-TOGGLEVIDEO-')
ZOOM = ('-FULL_VIDEO-', '-DETAIL_VIDEO-')
TIMERS = ('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-')


def clicked(win):
    # Welche Buttons loesen per Klick (Icon und Flaeche) ein Ereignis aus?
    result = set()
    for key in ALL:
        win[key].widget.invoke()
        events = drain(win)
        if key in win._btn_style:
            events += posted_by_click(win, win._btn_style[key]['containers'][1])
        if events:
            result.add(key)
    return result


@pytest.mark.parametrize('state, expected', [
    (dict(mode='live'), {*ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-'}),
    (dict(mode='live', has_recording=True), {*ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-'}),
    (dict(mode='live', zoom_level='detail'), {*ZOOM, '-RESETZOOM-', '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-'}),
    (dict(mode='live', zoom_center=(40, 50)), {*ZOOM, '-RESETZOOM-', '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-'}),
    (dict(mode='live', blink=True), {*ZOOM, '-BLINK_REF-', '-BLINK_STOP-', *TIMERS}),
    (dict(mode='blank', has_recording=True, zoom_level='detail'), {'-TOGGLEVIDEO-'}),
    (dict(mode='timer', blink=True, zoom_level='detail'), {'-TIMER_STOP-'}),
    (dict(mode='replay', blink=True, zoom_level='detail'), {*ZOOM, '-RESETZOOM-', '-REPLAY-'}),
])
def test_bedienbare_buttons_je_zustand(win, state, expected):
    ui.apply_controls(**state)
    assert clicked(win) == expected


def test_replay_knopf_wechselt_zu_replay_ende(win):
    ui.apply_controls('replay')
    win['-REPLAY-'].widget.invoke()
    assert win._queue.get_nowait()[0] == '-REPLAY_END-'
    assert win._btn_style['-REPLAY-']['label'].cget('text') == 'Replay Ende'
    ui.apply_controls('live', has_recording=True)
    win['-REPLAY-'].widget.invoke()
    assert win._queue.get_nowait()[0] == '-REPLAY-'
    assert win._btn_style['-REPLAY-']['label'].cget('text') == 'Replay'


def test_video_symbol_zeigt_den_zustand(win):
    ui.apply_controls('blank')
    blank = str(win['-TOGGLEVIDEO-'].widget.cget('image'))
    ui.apply_controls('live')
    assert str(win['-TOGGLEVIDEO-'].widget.cget('image')) != blank


def test_langer_standname_wird_gekuerzt(win):
    win.set_stand_name('Stand mit einem sehr, sehr langen Namen für die Kopfzeile')
    text = win['-STANDNAME-'].widget.cget('text')
    assert text.endswith('…')
    assert win._standname_font.measure(text) <= ui.STANDNAME_MAX_PX


def test_kurzer_standname_bleibt(win):
    win.set_stand_name('Stand 1')
    assert win['-STANDNAME-'].widget.cget('text') == 'Stand 1'
