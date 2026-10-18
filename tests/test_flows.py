# Dialogablaeufe aus flows.py ohne Hauptschleife: Ereignisse werden vorab
# (oder aus einer Fake-Kamera heraus) in die Warteschlange gelegt,
# Window.read() liest sie vor allen Tk-Ereignissen.
import subprocess
import time

import numpy as np
import pytest

import flows
import ui


class Cfg:
    def get_property(self, key):
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
        def get_frame(self, full=False):
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
        def get_frame(self, full=False):
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


def keypad(win, events):
    # Liefert, was beim abschliessenden OK eingegeben war.
    for event in events + ['-PIN_OK-']:
        win.post(event)
    return flows._run_pin_keypad('PIN eingeben', True, lambda entered: (True, entered))


def test_pin_tastenfeld_sammelt_ziffern(win):
    assert keypad(win, ['4', '7', '1', '1']) == '4711'


def test_pin_tastenfeld_hoechstens_sechs_ziffern(win):
    assert keypad(win, list('123456789')) == '123456'


def test_pin_tastenfeld_loeschen(win):
    assert keypad(win, ['9', '9', '-PIN_CLEAR-', '1', '2', '3', '4']) == '1234'


def test_pin_tastenfeld_zeigt_sternchen(win):
    for event in ['1', '2', '3']:
        win.post(event)
    win.post('-PIN_CANCEL-')
    flows._run_pin_keypad('PIN eingeben', True, lambda entered: (True, entered))
    assert win['-PINDISPLAY-'].widget.cget('text') == '***'


@pytest.mark.parametrize('event', ['', '12', '0123456789'])
def test_pin_tastenfeld_nimmt_nur_einzelne_ziffern(win, event):
    assert keypad(win, ['1', event, '2']) == '12'


# ------------------------------------------------------------------ Hilfen

def script(win, monkeypatch, steps):
    """Ersetzt Window.read durch eine feste Abfolge. Ein Schritt ist ein
    Ereignis, ein (Ereignis, Werte)-Paar oder eine Funktion, die vor dem
    naechsten Ereignis im Tk-Thread laeuft (z.B. eine Listbox-Auswahl)."""
    steps = list(steps)

    def read(timeout=None):
        while steps:
            step = steps.pop(0)
            if callable(step):
                step()
                continue
            return step if isinstance(step, tuple) else (step, {})
        return (ui.WIN_CLOSED, {})

    monkeypatch.setattr(win, 'read', read)
    return steps


def visible(win, key):
    return win[key].widget.winfo_manager() != ''


class FakeLimiter:
    def __init__(self):
        self.failures = 0
        self.successes = 0

    def register_failure(self):
        self.failures += 1
        return 0

    def register_success(self):
        self.successes += 1


@pytest.fixture
def limiter(monkeypatch):
    lim = FakeLimiter()
    monkeypatch.setattr(flows, 'PIN_LIMITER', lim)
    return lim


# ------------------------------------------------------------- PIN pruefen

def test_pin_richtig_oeffnet(win, monkeypatch, limiter):
    script(win, monkeypatch, ['4', '7', '1', '1', '-PIN_OK-'])
    assert flows.check_pin('4711') is True
    assert (limiter.failures, limiter.successes) == (0, 1)


def test_pin_falsch_oeffnet_nicht(win, monkeypatch, limiter):
    script(win, monkeypatch, ['1', '2', '3', '4', '-PIN_OK-', '-PIN_CANCEL-'])
    assert flows.check_pin('4711') is False
    assert limiter.failures == 1


def test_pin_erst_falsch_dann_richtig(win, monkeypatch, limiter):
    script(win, monkeypatch, ['1', '1', '1', '1', '-PIN_OK-', '4', '7', '1', '1', '-PIN_OK-'])
    assert flows.check_pin('4711') is True
    assert limiter.failures == 1


def test_pin_abbrechen_in_der_wartezeit_beendet_sofort(win, monkeypatch, limiter):
    # Ohne Wirkung von "Abbrechen" liefe die Wartezeit weiter und verbrauchte
    # die folgenden Ereignisse; '-UNGELESEN-' bliebe dann nicht uebrig.
    monkeypatch.setattr(limiter, 'register_failure', lambda: 30)
    rest = script(win, monkeypatch, ['1', '-PIN_OK-', '-PIN_CANCEL-', '-UNGELESEN-'])
    started = time.monotonic()
    assert flows.check_pin('4711') is False
    assert time.monotonic() - started < 5
    assert rest == ['-UNGELESEN-']


def test_pin_falsch_zeigt_hinweis(win, monkeypatch, limiter):
    monkeypatch.setattr(limiter, 'register_failure', lambda: 1.0)
    texts = []
    script(win, monkeypatch, ['1', '-PIN_OK-',
                              lambda: texts.append(win['-PINDISPLAY-'].widget.cget('text')),
                              '-PIN_CANCEL-'])
    flows.check_pin('4711')
    assert texts == ['falsch']


def test_pin_wartezeit_zeigt_die_restsekunden(win, monkeypatch, limiter):
    monkeypatch.setattr(limiter, 'register_failure', lambda: 2.5)
    texts = []

    def shown():
        texts.append(win['-PINDISPLAY-'].widget.cget('text'))

    script(win, monkeypatch, ['1', '-PIN_OK-', shown, lambda: time.sleep(1.0), '-VERWORFEN-',
                              shown, '-PIN_CANCEL-'])
    flows.check_pin('4711')
    assert texts == ['falsch 3s', 'falsch 2s']


# ---------------------------------------------------------- PIN aendern

@pytest.fixture
def saved_pins(monkeypatch):
    pins = []
    monkeypatch.setattr(flows, 'save_pin', lambda pin: pins.append(pin) or True)
    return pins


def test_pin_aendern(win, monkeypatch, limiter, saved_pins):
    script(win, monkeypatch, ['4', '7', '1', '1', '-PIN_OK-',          # aktuelle PIN
                              '2', '5', '8', '0', '-PIN_OK-',          # neue PIN
                              '2', '5', '8', '0', '-PIN_OK-'])         # Wiederholung
    assert flows.change_pin_flow('4711', forced=False) == '2580'
    assert saved_pins == ['2580']


def test_pin_aendern_ungleiche_wiederholung_fragt_neu(win, monkeypatch, limiter, saved_pins):
    # Nach "PINs stimmen nicht ueberein" geht es mit der neuen PIN weiter,
    # die aktuelle PIN wird nicht erneut abgefragt.
    script(win, monkeypatch, ['4', '7', '1', '1', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-',
                              '9', '9', '9', '9', '-PIN_OK-',
                              '3', '6', '9', '0', '-PIN_OK-',
                              '3', '6', '9', '0', '-PIN_OK-'])
    assert flows.change_pin_flow('4711', forced=False) == '3690'
    assert saved_pins == ['3690']
    assert limiter.successes == 1


def test_pin_aendern_meldet_ungleiche_wiederholung(win, monkeypatch, limiter, saved_pins):
    titles = []
    monkeypatch.setattr(flows.time, 'sleep', lambda s: titles.append(win['-PIN_TITLE-'].widget.cget('text')))
    script(win, monkeypatch, ['4', '7', '1', '1', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-',
                              '9', '9', '9', '9', '-PIN_OK-',
                              '-PIN_CANCEL-'])
    assert flows.change_pin_flow('4711', forced=False) is None
    assert titles == ['PINs stimmen nicht überein']


@pytest.mark.parametrize('steps', [
    ['-PIN_CANCEL-'],
    ['4', '7', '1', '1', '-PIN_OK-', '-PIN_CANCEL-'],
    ['4', '7', '1', '1', '-PIN_OK-', '2', '5', '8', '0', '-PIN_OK-', '-PIN_CANCEL-'],
])
def test_pin_aendern_abbrechen_in_jedem_schritt(win, monkeypatch, limiter, saved_pins, steps):
    script(win, monkeypatch, steps)
    assert flows.change_pin_flow('4711', forced=False) is None
    assert saved_pins == []


def test_pin_aendern_speicherfehler(win, monkeypatch, limiter):
    messages = []
    monkeypatch.setattr(flows, 'save_pin', lambda pin: False)
    monkeypatch.setattr(flows, 'popup', messages.append)
    script(win, monkeypatch, ['4', '7', '1', '1', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-'])
    assert flows.change_pin_flow('4711', forced=False) is None
    assert messages == ['PIN konnte nicht gespeichert werden.']


def test_pin_pflicht_ohne_abbrechen_danach_wieder_mit(win, monkeypatch, saved_pins):
    seen = []
    script(win, monkeypatch, [lambda: seen.append(visible(win, '-PIN_CANCEL-')),
                              '2', '5', '8', '0', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-'])
    assert flows.change_pin_flow('1234', forced=True) == '2580'
    assert seen == [False]
    win.root.update_idletasks()
    assert visible(win, '-PIN_CANCEL-')


def test_standard_pin_als_neue_pin_abgelehnt(win, monkeypatch, saved_pins):
    # Waehrend der 1,5 s Hinweis "zu einfach" verworfene Eingaben: die
    # Funktion wartet die Zeit ab, danach geht es mit leerer Eingabe weiter.
    texts = []
    script(win, monkeypatch, ['1', '2', '3', '4', '-PIN_OK-',
                              lambda: texts.append(win['-PINDISPLAY-'].widget.cget('text')),
                              lambda: time.sleep(1.6), '-VERWORFEN-',
                              '2', '5', '8', '0', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-'])
    assert flows.change_pin_flow('1234', forced=True) == '2580'
    assert texts == ['zu einfach']
    assert saved_pins == ['2580']


# ---------------------------------------------------------- Stand-Auswahl

STANDS = [{'id': 'stand1', 'displayName': 'Stand 1', 'url': 'rtmp://a'},
          {'id': 'stand2', 'displayName': 'Stand 2', 'url': 'rtmp://b'}]


def select(win, index):
    return lambda: win['-STAND_LIST-'].widget.selection_set(index)


def test_stand_auswahl_nach_position(win, monkeypatch):
    script(win, monkeypatch, [select(win, 1), '-STAND_SELECT-'])
    assert flows.run_stand_select(STANDS, forced=False) == STANDS[1]


def test_stand_auswahl_ohne_markierung_bleibt_offen(win, monkeypatch):
    script(win, monkeypatch, ['-STAND_SELECT-', select(win, 0), '-STAND_SELECT-'])
    assert flows.run_stand_select(STANDS, forced=False) == STANDS[0]


def test_stand_auswahl_abbrechen(win, monkeypatch):
    script(win, monkeypatch, ['-STAND_BACK-'])
    assert flows.run_stand_select(STANDS, forced=False) is None


def test_stand_auswahl_pflicht_ohne_abbrechen(win, monkeypatch):
    seen = []
    script(win, monkeypatch, [lambda: seen.append(visible(win, '-STAND_BACK-')),
                              '-STAND_BACK-', select(win, 0), '-STAND_SELECT-'])
    assert flows.run_stand_select(STANDS, forced=True) == STANDS[0]
    assert seen == [False]


def test_stand_auswahl_ohne_liste(win, monkeypatch):
    script(win, monkeypatch, [select(win, 0), '-STAND_SELECT-', '-STAND_BACK-'])
    assert flows.run_stand_select([], forced=False) is None
    assert 'keine Stand-Liste' in win['-STAND_LIST-'].widget.get(0)


# ------------------------------------------------------ Ausschnitte-Editor

class FrameCam:
    # 1000x620 fuellt den Editor-Canvas genau: Massstab 1, kein Versatz,
    # Canvas- und Bildkoordinaten sind gleich.
    def __init__(self, value=80):
        self.value = value

    def get_frame(self, full=False):
        return np.full((620, 1000, 3), self.value, np.uint8)


POINTS = [[200, 100], [800, 110], [210, 500], [790, 490]]


def drag(start, end):
    return [('-EDITGRAPH-', {'-EDITGRAPH-': start}),
            ('-EDITGRAPH-', {'-EDITGRAPH-': end}),
            '-EDITGRAPH-+UP']


def test_editor_speichern_ohne_aenderung(win, monkeypatch):
    script(win, monkeypatch, ['-EDIT_SAVE-'])
    assert np.allclose(flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS), POINTS)


def test_editor_ziehen_verschiebt_nur_den_gegriffenen_punkt(win, monkeypatch):
    script(win, monkeypatch, drag((205, 105), (250, 140)) + ['-EDIT_SAVE-'])
    result = flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS)
    assert np.allclose(result[0], [250, 140])
    assert np.allclose(result[1:], POINTS[1:])


def test_editor_klick_neben_die_punkte_verschiebt_nichts(win, monkeypatch):
    script(win, monkeypatch, drag((500, 300), (520, 320)) + ['-EDIT_SAVE-'])
    assert np.allclose(flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS), POINTS)


def test_editor_ziehen_bleibt_im_bild(win, monkeypatch):
    script(win, monkeypatch, drag((200, 100), (-50, -80)) + ['-EDIT_SAVE-'])
    assert np.allclose(flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS)[0], [0, 0])


def test_editor_neues_bild_behaelt_die_punkte(win, monkeypatch):
    cam = FrameCam()
    script(win, monkeypatch, drag((200, 100), (230, 120)) + [lambda: setattr(cam, 'value', 200),
                                                              '-EDIT_REFRESH-', '-EDIT_SAVE-'])
    result = flows.edit_section_points('Ganze Scheibe', cam, POINTS)
    assert np.allclose(result[0], [230, 120])


def test_editor_ungueltiges_viereck_bleibt_im_editor(win, monkeypatch):
    messages = []
    monkeypatch.setattr(flows, 'popup', messages.append)
    # Punkt 1 ueber Punkt 4 hinaus: ueberkreuztes Viereck.
    script(win, monkeypatch, drag((200, 100), (900, 550)) + ['-EDIT_SAVE-'] +
           drag((900, 550), (200, 100)) + ['-EDIT_SAVE-'])
    result = flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS)
    assert len(messages) == 1 and 'kein gültiges Viereck' in messages[0]
    assert np.allclose(result, POINTS)


def test_editor_abbrechen(win, monkeypatch):
    script(win, monkeypatch, drag((200, 100), (230, 120)) + ['-EDIT_CANCEL-'])
    assert flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS) is None


def test_editor_ohne_punkte_startet_mit_rechteck(win, monkeypatch):
    script(win, monkeypatch, ['-EDIT_SAVE-'])
    result = flows.edit_section_points('Ganze Scheibe', FrameCam(), None, allow_cancel=False, will_restart=False)
    assert np.allclose(result, [[200, 124], [800, 124], [200, 496], [800, 496]])


def test_editor_beschriftung_und_abbrechen_im_assistenten(win, monkeypatch):
    seen = []
    script(win, monkeypatch, [lambda: seen.append((win['-EDIT_SAVE-'].widget.cget('text'),
                                                   visible(win, '-EDIT_CANCEL-'))),
                              '-EDIT_SAVE-'])
    flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS, allow_cancel=False, will_restart=False)
    assert seen == [('Speichern', False)]


def test_editor_beschriftung_im_menue(win, monkeypatch):
    seen = []
    script(win, monkeypatch, [lambda: seen.append(win['-EDIT_SAVE-'].widget.cget('text')), '-EDIT_SAVE-'])
    flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS)
    assert seen == ['Speichern und\nneu starten']


class SizedCam:
    """Kamerabild in echter Groesse: der Editor verkleinert es und setzt es
    mittig in den Canvas, Canvas- und Kamerakoordinaten unterscheiden sich."""

    def __init__(self, width, height):
        self.shape = (height, width, 3)

    def get_frame(self, full=False):
        return np.full(self.shape, 80, np.uint8)


def canvas_mapping(width, height):
    # Massstab und Versatz wie im Editor, unabhaengig nachgerechnet.
    scale = min(ui.EDITOR_MAX_W / width, ui.EDITOR_MAX_H / height)
    off_x = (ui.EDITOR_MAX_W - int(width * scale)) // 2
    off_y = (ui.EDITOR_MAX_H - int(height * scale)) // 2
    return scale, off_x, off_y


# 2560x1920 (Kamera der Staende): Rand links und rechts. 2560x1000: Rand
# oben und unten.
@pytest.mark.parametrize('width, height', [(2560, 1920), (2560, 1000)])
def test_editor_rechnet_bei_echter_aufloesung_richtig_zurueck(win, monkeypatch, width, height):
    scale, off_x, off_y = canvas_mapping(width, height)
    assert off_x > 0 or off_y > 0
    points = [[500, 300], [2000, 320], [520, 900], [1980, 880]]
    grab = (points[0][0] * scale + off_x, points[0][1] * scale + off_y)
    target = (300, 200)
    script(win, monkeypatch, drag(grab, target) + ['-EDIT_SAVE-'])
    result = flows.edit_section_points('Ganze Scheibe', SizedCam(width, height), points)
    assert np.allclose(result[0], [(target[0] - off_x) / scale, (target[1] - off_y) / scale])
    assert np.allclose(result[1:], points[1:])


@pytest.mark.parametrize('width, height', [(2560, 1920), (2560, 1000)])
def test_editor_speichert_unveraenderte_punkte_bei_echter_aufloesung(win, monkeypatch, width, height):
    points = [[500, 300], [2000, 320], [520, 900], [1980, 880]]
    script(win, monkeypatch, ['-EDIT_SAVE-'])
    assert np.allclose(flows.edit_section_points('Ganze Scheibe', SizedCam(width, height), points), points)


def canvas_items(win, kind, **options):
    canvas = win.editor_canvas
    return [canvas.coords(i) for i in canvas.find_all()
            if canvas.type(i) == kind and all(canvas.itemcget(i, k) == v for k, v in options.items())]


def magnifier_corner(win):
    """Linke obere Ecke der Lupe (gelber Rahmen), oder None."""
    frames = canvas_items(win, 'rectangle', outline='yellow')
    return tuple(frames[0][:2]) if frames else None


@pytest.mark.parametrize('grab, end, corner', [
    # Punkt oben links -> Lupe unten rechts, und umgekehrt.
    ((200, 100), (210, 110), (ui.EDITOR_MAX_W - flows.MAG_SIZE - 10, ui.EDITOR_MAX_H - flows.MAG_SIZE - 10)),
    ((790, 490), (780, 480), (10, 10)),
])
def test_editor_lupe_in_der_gegenueberliegenden_ecke(win, monkeypatch, grab, end, corner):
    seen = []
    script(win, monkeypatch, [('-EDITGRAPH-', {'-EDITGRAPH-': grab}), ('-EDITGRAPH-', {'-EDITGRAPH-': end}),
                              lambda: seen.append(magnifier_corner(win)),
                              '-EDITGRAPH-+UP', lambda: seen.append(magnifier_corner(win)), '-EDIT_SAVE-'])
    flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS)
    # Waehrend des Ziehens sichtbar, nach dem Loslassen weg.
    assert seen == [corner, None]


def test_editor_zeigt_den_anderen_ausschnitt_grau(win, monkeypatch):
    seen = []
    script(win, monkeypatch, [lambda: seen.append(canvas_items(win, 'line', fill='#c0c0c0')), '-EDIT_SAVE-'])
    flows.edit_section_points('Innen Scheibe', FrameCam(), DETAIL, other_points=POINTS)
    gray_lines = seen[0]
    assert len(gray_lines) == 4
    # Die grauen Linien verbinden genau die Punkte des anderen Ausschnitts.
    ends = {tuple(map(round, line[i:i + 2])) for line in gray_lines for i in (0, 2)}
    assert ends == {tuple(p) for p in POINTS}


def test_editor_ohne_anderen_ausschnitt_keine_grauen_linien(win, monkeypatch):
    seen = []
    script(win, monkeypatch, [lambda: seen.append(canvas_items(win, 'line', fill='#c0c0c0')), '-EDIT_SAVE-'])
    flows.edit_section_points('Ganze Scheibe', FrameCam(), POINTS)
    assert seen == [[]]


def test_editor_ohne_kamerabild(win, monkeypatch):
    messages = []
    monkeypatch.setattr(flows, 'popup', messages.append)

    class NoCam:
        def get_frame(self, full=False):
            return None

    assert flows.edit_section_points('Ganze Scheibe', NoCam(), POINTS) is None
    assert len(messages) == 1


# ------------------------------------------------------ Einstellungen-Menue

@pytest.fixture
def saved(monkeypatch):
    calls = {'sections': [], 'stand': [], 'popup': []}
    monkeypatch.setattr(flows, 'save_sections_override',
                        lambda full, detail: calls['sections'].append((full, detail)) or True)
    monkeypatch.setattr(flows, 'save_active_stand', lambda sid: calls['stand'].append(sid) or True)
    monkeypatch.setattr(flows, 'popup', calls['popup'].append)
    return calls


DETAIL = [[400, 200], [600, 200], [400, 400], [600, 400]]


def test_menue_zurueck(win, monkeypatch, saved):
    script(win, monkeypatch, ['-MENU_BACK-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is False


@pytest.mark.parametrize('event, which', [('-MENU_FULL-', 0), ('-MENU_DETAIL-', 1)])
def test_menue_ausschnitt_speichern(win, monkeypatch, saved, event, which):
    script(win, monkeypatch, [event] + drag(tuple(POINTS[0]) if which == 0 else tuple(DETAIL[0]),
                                            (190, 95) if which == 0 else (390, 190)) + ['-EDIT_SAVE-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is True
    full, detail = saved['sections'][0]
    changed, kept = (full, detail) if which == 0 else (detail, full)
    assert np.allclose(changed[0], (190, 95) if which == 0 else (390, 190))
    assert np.allclose(kept, DETAIL if which == 0 else POINTS)


def test_menue_ausschnitt_abbrechen_zurueck_ins_menue(win, monkeypatch, saved):
    script(win, monkeypatch, ['-MENU_FULL-', '-EDIT_CANCEL-', '-MENU_BACK-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is False
    assert saved['sections'] == []


def test_menue_ausschnitt_speicherfehler_bleibt_im_menue(win, monkeypatch, saved):
    monkeypatch.setattr(flows, 'save_sections_override', lambda full, detail: False)
    script(win, monkeypatch, ['-MENU_FULL-', '-EDIT_SAVE-', '-MENU_BACK-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is False
    assert saved['popup'] == ['Speichern fehlgeschlagen - Änderung wurde NICHT übernommen.']


def test_menue_stand_wechseln(win, monkeypatch, saved):
    script(win, monkeypatch, ['-MENU_STAND-', select(win, 1), '-STAND_SELECT-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is True
    assert saved['stand'] == ['stand2']


def test_menue_stand_abbrechen_und_speicherfehler(win, monkeypatch, saved):
    monkeypatch.setattr(flows, 'save_active_stand', lambda sid: False)
    script(win, monkeypatch, ['-MENU_STAND-', '-STAND_BACK-',
                              '-MENU_STAND-', select(win, 0), '-STAND_SELECT-',
                              '-MENU_BACK-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is False
    assert saved['popup'] == ['Stand konnte nicht gespeichert werden.']


def test_menue_pin_aendern(win, monkeypatch, saved, limiter, saved_pins):
    script(win, monkeypatch, ['-MENU_PIN-',
                              '4', '7', '1', '1', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-',
                              '2', '5', '8', '0', '-PIN_OK-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is True
    assert saved_pins == ['2580']


def test_menue_pin_aendern_abbrechen_zurueck_ins_menue(win, monkeypatch, saved, limiter, saved_pins):
    pages = []
    real_show = flows.show_page
    monkeypatch.setattr(flows, 'show_page', lambda key: (pages.append(key), real_show(key))[1])
    script(win, monkeypatch, ['-MENU_PIN-',
                              '4', '7', '1', '1', '-PIN_OK-',
                              '-PIN_CANCEL-',
                              '-MENU_BACK-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is False
    assert saved_pins == []
    # Nach dem Abbrechen wieder das Menue, erst "Zurueck" fuehrt hinaus.
    assert pages[-2:] == ['-MENUVIEW-', '-MAINVIEW-']


def test_menue_neustart_abbrechen(win, monkeypatch, saved):
    calls = []
    monkeypatch.setattr(flows.subprocess, 'run', lambda *a, **k: calls.append(a))
    script(win, monkeypatch, ['-MENU_RESTART-', '-CONFIRM_NO-', '-MENU_BACK-'])
    assert flows.run_settings_flow(FrameCam(), POINTS, DETAIL, STANDS, '4711') is False
    assert calls == []


def test_with_section_ersetzt_nur_den_gewaehlten_ausschnitt():
    assert flows.with_section('full', 'neu', 'ganz', 'innen') == ('neu', 'innen')
    assert flows.with_section('detail', 'neu', 'ganz', 'innen') == ('ganz', 'neu')
