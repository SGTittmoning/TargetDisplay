"""Rauchtest der gesamten Anwendung unter einem X-Server (Xvfb).

Startet main.main() mit einer Fake-Kamera und steuert die Oberflaeche wie ein
Bediener: Timer, Video aus/ein, PIN-Eingabe mit falscher und richtiger PIN,
Einstellungsmenue, Replay, Ersteinrichtung. Ausfuehren mit:
xvfb-run -a python -m pytest tests
"""
import itertools
import time

import numpy as np
from app_harness import (
    CountingCamera,
    FakeCamera,
    button_states,
    is_replay_frame,
    is_timer_frame,
    load_app,
    needs_display,
    run_app,
)

pytestmark = needs_display


def test_video_timer_und_video_aus(app, monkeypatch):
    steps = [
        (1.0, '-FULL_VIDEO-'),
        (0.3, '-DETAIL_VIDEO-'),
        (0.3, '-TOGGLEVIDEO-'),      # Video aus
        (0.5, '-TOGGLEVIDEO-'),      # Video ein
        (0.3, '-TIMER_10-'),         # Timer-Serie
        (2.5, '-TIMER_STOP-'),
        (0.5, '-BLINK_START-'),
        (1.0, '-BLINK_STOP-'),
    ]
    draws = run_app(app, steps, monkeypatch)
    assert len(draws) > 10
    # Die Timer-Anzeige ist rot (Vorbereitung): BGR (0, 0, 255)
    assert any((d[100, 100] == (0, 0, 255)).all() for d in draws)


def test_pin_falsch_dann_richtig_und_einstellungsmenue(app, monkeypatch):
    import flows
    from pinlock import PinLimiter
    limiter = PinLimiter()
    failures = []
    monkeypatch.setattr(limiter, 'register_failure', lambda: failures.append(1) or 0.3)
    monkeypatch.setattr(flows, 'PIN_LIMITER', limiter)
    opened = []
    real_settings = app.run_settings_flow
    monkeypatch.setattr(app, 'run_settings_flow', lambda *a, **k: (opened.append(True), real_settings(*a, **k))[1])
    steps = [
        (1.0, '-SETTINGS-'),
        (0.4, '1'), (0.1, '1'), (0.1, '1'), (0.1, '1'), (0.1, '-PIN_OK-'),   # falsch
        (0.6, '4'), (0.1, '7'), (0.1, '1'), (0.1, '1'), (0.1, '-PIN_OK-'),   # richtig
        (0.6, '-MENU_BACK-'),
    ]
    run_app(app, steps, monkeypatch)
    # Die falsche PIN wurde abgewiesen, erst die richtige oeffnet das Menue.
    assert failures == [1]
    assert opened == [True]


def test_pin_abbrechen_waehrend_wartezeit(app, monkeypatch):
    import flows
    from pinlock import PinLimiter
    limiter = PinLimiter()
    monkeypatch.setattr(limiter, 'register_failure', lambda: 30)
    monkeypatch.setattr(flows, 'PIN_LIMITER', limiter)
    # Gemessen wird nur die Wartezeit nach der falschen PIN, nicht der
    # App-Start (auf langsamen CI-Runnern allein mehrere Sekunden).
    waits = []
    real_wait = flows._wait_after_error

    def wait(*args):
        started = time.monotonic()
        result = real_wait(*args)
        waits.append((result, time.monotonic() - started))
        return result

    monkeypatch.setattr(flows, '_wait_after_error', wait)
    steps = [
        (1.0, '-SETTINGS-'),
        (0.4, '1'), (0.1, '-PIN_OK-'),
        (0.5, '-PIN_CANCEL-'),   # waehrend der 30 s Wartezeit
    ]
    run_app(app, steps, monkeypatch)
    # Abgebrochen (False) lange vor Ablauf der 30 s.
    assert len(waits) == 1
    result, duration = waits[0]
    assert result is False and duration < 10



def test_timer_waehrend_blinken(app, monkeypatch):
    monkeypatch.setattr(app, 'Camera', CountingCamera)
    keys = ('-BLINK_START-', '-BLINK_REF-', '-BLINK_STOP-', '-TIMER_10-', '-TIMER_STOP-')
    states = button_states(monkeypatch, keys)
    steps = [
        (1.0, '-BLINK_START-'),
        (0.5, '-TIMER_10-'),      # Timer trotz Blinken startbar
        (1.5, '-TIMER_STOP-'),
        (2.5, '-FULL_VIDEO-'),
    ]
    draws = run_app(app, steps, monkeypatch)
    timer_idx = [i for i, d in enumerate(draws) if is_timer_frame(d)]
    assert timer_idx, 'Timer wurde nicht angezeigt'
    before = [int(d[300, 300, 0]) for d in draws[:timer_idx[0]]]
    after = [int(d[300, 300, 0]) for d in draws[timer_idx[-1] + 1:]]
    # Nach dem Timer blinkt das Bild mit der Referenz von VOR dem Timer
    # weiter: ein Teil der Bilder ist aelter als der Timer-Start.
    assert after and min(after) <= max(before)
    assert max(after) > max(before)
    # Blinken laeuft weiter, Timer wieder startbar
    assert states['-BLINK_STOP-'] and states['-BLINK_REF-']
    assert not states['-BLINK_START-']
    assert states['-TIMER_10-'] and not states['-TIMER_STOP-']


def test_timer_ohne_blinken_gibt_blinken_frei(app, monkeypatch):
    keys = ('-BLINK_START-', '-BLINK_REF-', '-BLINK_STOP-')
    states = button_states(monkeypatch, keys)
    run_app(app, [(1.0, '-TIMER_10-'), (1.0, '-TIMER_STOP-')], monkeypatch)
    assert states['-BLINK_START-']
    assert not states['-BLINK_REF-'] and not states['-BLINK_STOP-']


def test_replay_nach_timer(app, monkeypatch):
    import timerlib
    # Kurze Vorbereitungszeit, damit die Gruenphase im Test schnell beginnt.
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    keys = ('-REPLAY-', '-TIMER_10-', '-FULL_VIDEO-', '-BLINK_START-')
    states = button_states(monkeypatch, keys)
    steps = [
        (1.0, '-TIMER_10-'),
        (3.5, '-TIMER_STOP-'),     # ca. 1,5 s Gruen aufgenommen (Verzoegerung 1 s)
        (0.5, '-REPLAY-'),
        (1.5, '-DETAIL_VIDEO-'),   # Zoom im Replay (nach dem Startbild)
        (0.5, '-REPLAY-'),         # erneut abspielen
        (2.0, '-REPLAY_END-'),
        (0.8, '-FULL_VIDEO-'),
    ]
    draws = run_app(app, steps, monkeypatch)
    replay_idx = [i for i, d in enumerate(draws) if is_replay_frame(d)]
    assert len(replay_idx) > 5
    # Nach "Replay Ende" wieder Livebild ohne Replay-Rahmen
    assert not is_replay_frame(draws[-1])
    # Aufnahme bleibt nach "Replay Ende" fuer ein weiteres Replay erhalten
    assert states['-REPLAY-'] and states['replay_text'] == 'Replay'
    assert states['-TIMER_10-'] and states['-FULL_VIDEO-'] and states['-BLINK_START-']


def test_replay_endet_nach_rotem_schlussbild(app, monkeypatch):
    import timerlib
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    states = button_states(monkeypatch, ('-REPLAY-', '-TIMER_10-'))
    steps = [
        (1.0, '-TIMER_10-'),
        (3.0, '-TIMER_STOP-'),
        (0.3, '-REPLAY-'),
        (4.5, '-FULL_VIDEO-'),     # Replay (ca. 1 s + 1 s Rot) ist laengst vorbei
    ]
    draws = run_app(app, steps, monkeypatch)
    assert any(is_replay_frame(d) for d in draws)
    assert not is_replay_frame(draws[-1])
    assert states['-REPLAY-'] and states['replay_text'] == 'Replay' and states['-TIMER_10-']


def test_replay_button_wird_zu_replay_ende(app, monkeypatch):
    import timerlib
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    states = button_states(monkeypatch, ('-REPLAY-',))
    steps = [
        (1.0, '-TIMER_10-'),
        (3.0, '-TIMER_STOP-'),
        (0.3, '-REPLAY-'),
        (0.5, '-FULL_VIDEO-'),     # Replay laeuft noch
    ]
    run_app(app, steps, monkeypatch)
    assert states['-REPLAY-'] and states['replay_text'] == 'Replay Ende'


def test_replay_gesperrt_bei_video_aus(app, monkeypatch):
    import timerlib
    monkeypatch.setattr(timerlib, 'PREP_TIME', 1)
    states = button_states(monkeypatch, ('-REPLAY-',))
    steps = [
        (1.0, '-TIMER_10-'),
        (3.0, '-TIMER_STOP-'),
        (0.3, '-TOGGLEVIDEO-'),    # Video aus
        (0.3, '-REPLAY-'),         # wirkungslos, Button gesperrt
    ]
    draws = run_app(app, steps, monkeypatch)
    assert not any(is_replay_frame(d) for d in draws)
    assert not states['-REPLAY-'] and states['replay_text'] == 'Replay'


def test_kein_replay_ohne_aufnahme(app, monkeypatch):
    states = button_states(monkeypatch, ('-REPLAY-',))
    run_app(app, [(1.0, '-FULL_VIDEO-')], monkeypatch)
    assert not states['-REPLAY-'] and states['replay_text'] == 'Replay'


# ------------------------------------------------- Ersteinrichtungs-Assistent

WIZARD_CONFIG = """\
screenSize: [1024, 768]
video:
  size:
    x: 600
    y: 600
"""

WIZARD_STANDS = [{'id': 'stand1', 'displayName': 'Stand 1', 'url': 'rtmp://eins'},
                 {'id': 'stand2', 'displayName': 'Stand 2', 'url': 'rtmp://zwei'}]
WIZARD_FULL = [[100, 100], [500, 100], [100, 500], [500, 500]]
WIZARD_DETAIL = [[200, 200], [400, 200], [200, 400], [400, 400]]


def test_ersteinrichtung_stand_ausschnitte_pin(tmp_path, monkeypatch):
    # Neues Geraet ohne Stand, Ausschnitte und PIN. Ablauf: Stand 1 waehlen,
    # Kamera antwortet nicht -> zurueck, Stand 2 waehlen, Ganze und Innen
    # Scheibe kalibrieren, PIN festlegen, danach der Hauptbildschirm.
    import json
    main = load_app(tmp_path, monkeypatch, WIZARD_CONFIG)
    import settings_store
    with open(settings_store.STANDS_FILE, 'w') as f:
        json.dump({'stands': WIZARD_STANDS}, f)

    log = []
    cameras = []

    class Cam(FakeCamera):
        def __init__(self, url, **kwargs):
            super().__init__(url)
            self.stopped = False
            cameras.append(self)

        def stop(self):
            self.stopped = True

    chosen = iter(WIZARD_STANDS)
    frames = itertools.chain(['back'], itertools.repeat(np.zeros((700, 700, 3), np.uint8)))
    edits = iter([WIZARD_FULL, WIZARD_DETAIL])

    def stand_select(stands, forced):
        log.append(('stand', forced, [s['id'] for s in stands]))
        return next(chosen)

    def save_active_stand(stand_id):
        log.append(('stand gespeichert', stand_id))
        with open(settings_store.ACTIVE_STAND_FILE, 'w') as f:
            json.dump({'id': stand_id}, f)
        return True

    def wait_for_frame(cap):
        log.append(('warten', cap.url))
        return next(frames)

    def edit(label, cap, points, other_points=None, allow_cancel=True, will_restart=True):
        log.append(('editor', label, other_points, allow_cancel, will_restart))
        return next(edits)

    def save_sections(full, detail):
        log.append(('ausschnitte gespeichert', full, detail))
        with open(settings_store.SECTIONS_OVERRIDE_FILE, 'w') as f:
            json.dump({'section_full': full, 'section_detail': detail}, f)
        return True

    def change_pin(current, forced):
        log.append(('pin', current, forced))
        return '2580'

    monkeypatch.setattr(main, 'Camera', Cam)
    monkeypatch.setattr(main, 'run_stand_select', stand_select)
    monkeypatch.setattr(main, 'save_active_stand', save_active_stand)
    monkeypatch.setattr(main, 'wait_for_camera_frame', wait_for_frame)
    monkeypatch.setattr(main, 'edit_section_points', edit)
    monkeypatch.setattr(main, 'save_sections_override', save_sections)
    monkeypatch.setattr(main, 'change_pin_flow', change_pin)

    draws = run_app(main, [(1.0, '-FULL_VIDEO-')], monkeypatch)

    assert log == [
        ('stand', True, ['stand1', 'stand2']),
        ('stand gespeichert', 'stand1'),
        ('warten', 'rtmp://eins'),
        ('stand', True, ['stand1', 'stand2']),
        ('stand gespeichert', 'stand2'),
        ('warten', 'rtmp://zwei'),
        ('editor', 'Ganze Scheibe', None, False, False),
        ('ausschnitte gespeichert', WIZARD_FULL, None),
        ('warten', 'rtmp://zwei'),
        ('editor', 'Innen Scheibe', WIZARD_FULL, False, False),
        ('ausschnitte gespeichert', WIZARD_FULL, WIZARD_DETAIL),
        ('pin', '1234', True),
    ]
    # Die Verbindung zum verworfenen Stand wurde beendet.
    assert [(c.url, c.stopped) for c in cameras] == [('rtmp://eins', True), ('rtmp://zwei', False)]
    # Danach laeuft der Hauptbildschirm mit Livebild und dem Zuschnitt aus
    # beiden Ausschnitten (plus Rand).
    assert len(draws) > 3
    assert cameras[1].crop_region == (85, 85, 515, 515)


def test_ersteinrichtung_ueberspringt_vorhandene_schritte(tmp_path, monkeypatch):
    # Stand, Ausschnitte und PIN sind schon gespeichert (z.B. Neustart nach
    # Stromausfall): kein Schritt des Assistenten erscheint.
    import json
    main = load_app(tmp_path, monkeypatch, WIZARD_CONFIG)
    import settings_store
    for path, data in ((settings_store.STANDS_FILE, {'stands': WIZARD_STANDS}),
                       (settings_store.ACTIVE_STAND_FILE, {'id': 'stand2'}),
                       (settings_store.SECTIONS_OVERRIDE_FILE,
                        {'section_full': WIZARD_FULL, 'section_detail': WIZARD_DETAIL}),
                       (settings_store.PIN_FILE, {'pin': '2580'})):
        with open(path, 'w') as f:
            json.dump(data, f)

    def unexpected(*args, **kwargs):
        raise AssertionError('Assistent haette nicht erscheinen duerfen')

    for name in ('run_stand_select', 'wait_for_camera_frame', 'edit_section_points', 'change_pin_flow'):
        monkeypatch.setattr(main, name, unexpected)
    draws = run_app(main, [(1.0, '-FULL_VIDEO-')], monkeypatch)
    assert len(draws) > 3
