import pytest
from app_harness import needs_display, open_window

import flows
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
    for key, button in win.accent_buttons.items():
        outer = button.frame
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
    button = win.accent_buttons['-TIMER_10-']
    widget = {'frame': button.frame, 'inner': button.inner,
              'label': button.label, 'button': button.button}[part]
    if part == 'button':
        widget.invoke()
        assert win._queue.get_nowait()[0] == '-TIMER_10-'
    else:
        assert posted_by_click(win, widget) == ['-TIMER_10-']


ALL = ('-FULL_VIDEO-', '-DETAIL_VIDEO-', '-RESETZOOM-', '-BLINK_START-', '-BLINK_REF-',
       '-BLINK_STOP-', '-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-', '-TIMER_STOP-',
       '-REPLAY-', '-TOGGLEVIDEO-', '-SETTINGS-')
ZOOM = ('-FULL_VIDEO-', '-DETAIL_VIDEO-')
TIMERS = ('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-')


def clicked(win):
    # Welche Buttons loesen per Klick (Icon und Flaeche) ein Ereignis aus?
    result = set()
    for key in ALL:
        win[key].widget.invoke()
        events = drain(win)
        if key in win.accent_buttons:
            events += posted_by_click(win, win.accent_buttons[key].frame)
        if events:
            result.add(key)
    return result


@pytest.mark.parametrize('state, expected', [
    (dict(mode='live'), {*ZOOM, '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-', '-SETTINGS-'}),
    (dict(mode='live', has_recording=True), {*ZOOM, '-BLINK_START-', *TIMERS, '-REPLAY-', '-TOGGLEVIDEO-', '-SETTINGS-'}),
    (dict(mode='live', zoom_level='detail'), {*ZOOM, '-RESETZOOM-', '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-', '-SETTINGS-'}),
    (dict(mode='live', zoom_center=(40, 50)), {*ZOOM, '-RESETZOOM-', '-BLINK_START-', *TIMERS, '-TOGGLEVIDEO-', '-SETTINGS-'}),
    (dict(mode='live', blink=True), {*ZOOM, '-BLINK_REF-', '-BLINK_STOP-', *TIMERS, '-SETTINGS-'}),
    (dict(mode='blank', has_recording=True, zoom_level='detail'), {'-TOGGLEVIDEO-', '-SETTINGS-'}),
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
    assert win.accent_buttons['-REPLAY-'].text == 'Replay Ende'
    ui.apply_controls('live', has_recording=True)
    win['-REPLAY-'].widget.invoke()
    assert win._queue.get_nowait()[0] == '-REPLAY-'
    assert win.accent_buttons['-REPLAY-'].text == 'Replay'


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


def test_tipp_auf_version_oder_infozeile_meldet_info(win):
    version_row = win['-FPS-'].widget.master
    version = next(w for w in version_row.winfo_children() if str(w.cget('text')).startswith('V: '))
    for widget in (version, version_row, win['-USAGE-'].widget, win['-TEMP-'].widget.master):
        assert posted_by_click(win, widget) == ['-INFO-']


def test_infozeile_ein_und_aus(win):
    ui.show_info(True, usage='CPU 1 %   RAM 2 %', temperature='81 °C', level='hot', load='Last 1,00')
    win.root.update()
    assert win['-FPS-'].widget.winfo_manager() != ''
    assert win['-TEMP-'].widget.cget('text') == '81 °C'
    assert win['-TEMP-'].widget.cget('fg') == ui.TEMP_COLORS['hot']
    ui.show_info(False)
    win.root.update()
    assert win['-FPS-'].widget.winfo_manager() == ''
    assert [win[k].widget.cget('text') for k in ('-USAGE-', '-TEMP-', '-LOAD-')] == ['', '', '']


def test_auswaehlen_erst_mit_markiertem_stand(win):
    win.post('-STAND_BACK-')
    flows.run_stand_select([{'id': 'a', 'displayName': 'Stand A', 'url': 'x'},
                            {'id': 'b', 'displayName': 'Stand B', 'url': 'y'}], forced=False)
    button, listbox = win['-STAND_SELECT-'].widget, win['-STAND_LIST-'].widget
    assert str(button.cget('state')) == 'disabled'
    assert button.cget('bg') == ui.ACCENT_ZOOM_MUTED_BG
    listbox.selection_set(1)
    listbox.event_generate('<<ListboxSelect>>')
    win.root.update()
    assert str(button.cget('state')) == 'normal'
    assert button.cget('bg') == ui.ACCENT_ZOOM


def test_popup_zeigt_text_und_schliesst_mit_ok(win):
    seen = []

    def press_ok():
        top = next(w for w in win.root.winfo_children() if isinstance(w, ui.tk.Toplevel))
        seen.extend(c.cget('text') for c in top.winfo_children())
        next(c for c in top.winfo_children() if c.cget('text') == 'OK').invoke()

    win.root.after(200, press_ok)
    win.popup('Speichern fehlgeschlagen.')
    assert seen == ['Speichern fehlgeschlagen.', 'OK']
    # Danach ist das Fenster wieder bedienbar (kein Grab mehr aktiv).
    assert win.root.grab_current() is None


@pytest.mark.parametrize('x, y, expected', [(390, 195, (50.0, 25.0)), (0, 780, (0.0, 100.0))])
def test_tipp_ins_bild_wird_in_prozent_umgerechnet(win, x, y, expected):
    win.video_canvas.event_generate('<Button-1>', x=x, y=y)
    win.root.update()
    assert win._queue.get_nowait() == ('-VIDEO-', {'-VIDEO-': expected})
