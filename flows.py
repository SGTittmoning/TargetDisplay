"""Dialog-Ablaeufe auf der Oberflaeche: PIN, Stand-Auswahl, Kamera-Warten,
Einstellungen. Der Ausschnitte-Editor steht in section_editor.py."""
import math
import subprocess
import sys
import time

import ui
from camera import STREAM_STARTUP_TIMEOUT_SEC
from pinlock import PinLimiter
from section_editor import edit_section_points
from settings_store import (
    DEFAULT_PIN,
    PIN_MAX_LEN,
    PIN_MIN_LEN,
    save_active_stand,
    save_pin,
    save_sections_override,
)
from ui import FG_DARK, WIN_CLOSED, popup, show_page


def _wait_after_error(error_text, wait_s):
    # Zeigt error_text rot und wartet wait_s Sekunden, ohne die Oberflaeche
    # einzufrieren. Tastendruecke waehrend der Wartezeit werden verworfen.
    # Liefert False, wenn waehrenddessen abgebrochen wurde (Abbrechen/Fenster
    # zu), sonst True.
    end = time.monotonic() + wait_s
    while True:
        remaining = end - time.monotonic()
        if remaining <= 0:
            return True
        text = error_text if wait_s < 2 else f'{error_text} {math.ceil(remaining)}s'
        ui.window['-PINDISPLAY-'].update(text, text_color='red')
        event, _ = ui.window.read(timeout=200)
        if event in (WIN_CLOSED, '-PIN_CANCEL-'):
            return False


# Ereignisse der Zifferntasten. Eine Menge statt eines Strings: "in" auf
# einem String traefe auch Teilstrings wie '12'.
PIN_DIGITS = frozenset('0123456789')


def _run_pin_keypad(title, show_cancel, validate):
    # Tastenfeld fuer check_pin() und _enter_new_pin(). validate(entered)
    # liefert bei OK (True, ergebnis) oder (False, (fehlertext, sekunden)); der
    # Fehlertext steht dann so lange rot da (ab 2 s mit Restzeit), danach geht
    # es mit leerer Eingabe weiter. Liefert das Ergebnis oder None bei
    # Abbrechen.
    ui.window['-PIN_TITLE-'].update(title)
    ui.window['-PIN_CANCEL-'].update(visible=show_cancel)
    show_page('-PINVIEW-')
    ui.window['-PINDISPLAY-'].update('', text_color=FG_DARK)
    entered = ''
    result = None
    while True:
        event, _ = ui.window.read()
        if event in (WIN_CLOSED, '-PIN_CANCEL-'):
            break
        elif event == '-PIN_CLEAR-':
            entered = ''
        elif event == '-PIN_OK-':
            ok, value = validate(entered)
            if ok:
                result = value
                break
            else:
                error_text, sleep_s = value
                entered = ''
                if not _wait_after_error(error_text, sleep_s):
                    break
        elif event in PIN_DIGITS:
            if len(entered) < PIN_MAX_LEN:
                entered += event
        ui.window['-PINDISPLAY-'].update('*' * len(entered), text_color=FG_DARK)
    return result


PIN_LIMITER = PinLimiter()


def check_pin(correct_pin):
    # PIN-Abfrage vor den Einstellungen. Nach jedem Fehlversuch eine laenger
    # werdende Pause (pinlock.py): schreckt zufaelliges Herumtippen ab, ist aber
    # kein Schutz gegen gezieltes Durchprobieren.
    # Bei richtiger PIN bleibt die Seite stehen, bis der Aufrufer die naechste
    # zeigt (sonst blitzte der Hauptbildschirm kurz auf); bei Abbrechen geht es
    # zurueck zum Hauptbildschirm.
    def validate(entered):
        if entered == correct_pin:
            PIN_LIMITER.register_success()
            return True, True
        return False, ('falsch', PIN_LIMITER.register_failure())
    result = bool(_run_pin_keypad('PIN eingeben', True, validate))
    if not result:
        show_page('-MAINVIEW-')
    return result


def _reboot():
    # True, wenn der Neustart ausgeloest wurde. Scheitert sudo, bliebe die
    # Standaufsicht sonst ohne Rueckmeldung im Menue.
    try:
        result = subprocess.run(['/usr/bin/sudo', '/usr/sbin/reboot'], check=False)
    except OSError as e:
        print(f"Neustart fehlgeschlagen: {e}", file=sys.stderr)
        return False
    if result.returncode != 0:
        print(f"Neustart fehlgeschlagen: Exit-Code {result.returncode}", file=sys.stderr)
        return False
    return True


def confirm_reboot():
    # Wechselt die Seite nicht selbst; nach Abbrechen entscheidet der Aufrufer,
    # wohin es zurueckgeht.
    show_page('-CONFIRMVIEW-')
    event, _ = ui.window.read()
    return event == '-CONFIRM_YES-'


def validate_new_pin(entered):
    # Prueft eine neue PIN: 4-6 Ziffern und nicht der Standard-PIN (den wuerde
    # der Assistent sofort wieder zur Aenderung vorlegen). Liefert (True, entered) oder
    # (False, (Fehlertext, Wartezeit_s)) wie _run_pin_keypad() erwartet. Das
    # Anzeigefeld fasst rund 11 Zeichen.
    if not PIN_MIN_LEN <= len(entered) <= PIN_MAX_LEN:
        return False, ('4-6 Ziffern', 0.8)
    if entered == DEFAULT_PIN:
        return False, ('zu einfach', 1.5)
    return True, entered


def _enter_new_pin(title, show_cancel):
    # Liefert die eingetippte Ziffernfolge oder None (Abbrechen), ohne
    # Vergleich mit einer gespeicherten PIN. change_pin_flow() ruft dies fuer
    # Eingabe und Wiederholung auf.
    result = _run_pin_keypad(title, show_cancel, validate_new_pin)
    # Seite fuer die naechste Nutzung (check_pin) wieder in den
    # Grundzustand versetzen.
    ui.window['-PIN_TITLE-'].update('PIN eingeben')
    ui.window['-PIN_CANCEL-'].update(visible=True)
    return result


def change_pin_flow(forced):
    # forced=True: Assistent, solange der Standard-PIN gilt; kein Abbrechen.
    # forced=False: Aenderung ueber das Menue, abbrechbar. Die aktuelle PIN
    # wurde schon vor dem Menue abgefragt.
    while True:
        new1 = _enter_new_pin('Neuen PIN eingeben (4-6 Ziffern)', show_cancel=not forced)
        if new1 is None:
            return None
        new2 = _enter_new_pin('PIN wiederholen', show_cancel=not forced)
        if new2 is None:
            return None
        if new1 == new2:
            break
        ui.window['-PIN_TITLE-'].update('PINs stimmen nicht überein')
        ui.window.refresh()
        time.sleep(1.0)
    if save_pin(new1):
        return new1
    popup('PIN konnte nicht gespeichert werden.')
    return None


def run_stand_select(stands, forced):
    # forced=True: Assistent, noch kein Stand gewaehlt; kein Abbrechen.
    # forced=False: Stand-Wechsel ueber das Menue, abbrechbar.
    show_page('-STANDVIEW-')
    ui.window['-STAND_BACK-'].update(visible=not forced)
    if not stands:
        # Ohne Stand-Liste (z.B. noch nicht per Ansible verteilt) eine klare
        # Meldung statt einer leeren Auswahl.
        ui.window['-STAND_LIST-'].update(values=['(keine Stand-Liste gefunden - bitte Installation prüfen)'])
        ui.window.set_stand_select_enabled(False)
    else:
        # update(values=...) leert die Liste samt Markierung; Auswaehlen wird
        # mit der ersten Markierung bedienbar (siehe ui._build_stand_view).
        ui.window['-STAND_LIST-'].update(values=[s['displayName'] for s in stands])
        ui.window.set_stand_select_enabled(False)
    ui.window.refresh()
    while True:
        event, values = ui.window.read()
        if event == WIN_CLOSED:
            return None
        elif event == '-STAND_BACK-' and not forced:
            return None
        elif event == '-STAND_SELECT-' and stands:
            # Nach Position, nicht nach Name: zwei Staende koennten gleich heissen.
            sel = ui.window['-STAND_LIST-'].widget.curselection()
            if sel:
                return stands[sel[0]]


def wait_for_camera_frame(cap, max_wait_sec=STREAM_STARTUP_TIMEOUT_SEC):
    # Wartet auf den ersten Frame nach der Stand-Auswahl. Die Kamera versucht
    # im Hintergrund laufend neu zu verbinden; diese Seite bleibt dabei
    # bedienbar und kehrt zurueck, sobald ein Frame da ist. Nach
    # max_wait_sec erscheint ein Hinweis, "Erneut versuchen" startet die
    # Wartezeit neu. Gibt den Frame zurueck, oder None fuer "zurueck zur
    # Stand-Auswahl".
    show_page('-CAMWAITVIEW-')

    def start_waiting():
        ui.window['-CAMWAIT_TEXT-'].update('Verbinde mit Kamera...')
        ui.window.refresh()
        return time.monotonic() + max_wait_sec

    deadline = start_waiting()
    timed_out = False
    while True:
        frame = cap.get_frame(full=True)
        if frame is not None:
            return frame
        if not timed_out and time.monotonic() >= deadline:
            timed_out = True
            ui.window['-CAMWAIT_TEXT-'].update('Kamera nicht erreichbar.\nBitte URL/Verkabelung prüfen.')
            ui.window.refresh()
        event, _ = ui.window.read(timeout=300)
        if event in (WIN_CLOSED, '-CAMWAIT_BACK-'):
            return None
        if event == '-CAMWAIT_RETRY-':
            deadline = start_waiting()
            timed_out = False


# Beschriftung der beiden Ausschnitte im Editor.
SECTION_LABELS = {'full': 'Ganze Scheibe', 'detail': 'Innen Scheibe'}


def with_section(which, points, section_full, section_detail):
    """(section_full, section_detail), darin `which` durch `points` ersetzt."""
    return (points, section_detail) if which == 'full' else (section_full, points)


def run_settings_flow(cap, section_full, section_detail, stands):
    # Einstellungen-Menue. section_full/section_detail sind Kamerakoordinaten
    # des vollen Bilds, wie sie gespeichert werden. Liefert True, wenn etwas
    # gespeichert wurde (Ausschnitte, Stand oder PIN); die App beendet sich
    # dann (MainScreen.open_settings) und systemd startet sie mit den neuen
    # Werten neu. Auch die PIN laeuft ueber
    # diesen Neustart: ein Weg fuer alles ist weniger fehleranfaellig.
    # Abbrechen oder ein Fehler in einer Unteraktion fuehrt zurueck in dieses
    # Menue, nicht zum Hauptbildschirm.
    while True:
        show_page('-MENUVIEW-')
        event, _ = ui.window.read()
        if event in (WIN_CLOSED, '-MENU_BACK-'):
            show_page('-MAINVIEW-')
            return False
        elif event in ('-MENU_FULL-', '-MENU_DETAIL-'):
            which = 'full' if event == '-MENU_FULL-' else 'detail'
            current, other = ((section_full, section_detail) if which == 'full'
                              else (section_detail, section_full))
            result = edit_section_points(SECTION_LABELS[which], cap, current, other_points=other)
            if result is None:
                continue
            if not save_sections_override(*with_section(which, result, section_full, section_detail)):
                popup('Speichern fehlgeschlagen - Änderung wurde NICHT übernommen.')
                continue
            show_page('-MAINVIEW-')
            return True
        elif event == '-MENU_STAND-':
            chosen = run_stand_select(stands, forced=False)
            if chosen is None:
                continue
            if not save_active_stand(chosen['id']):
                popup('Stand konnte nicht gespeichert werden.')
                continue
            show_page('-MAINVIEW-')
            return True
        elif event == '-MENU_PIN-':
            new_pin = change_pin_flow(forced=False)
            if new_pin is None:
                continue
            show_page('-MAINVIEW-')
            return True
        elif event == '-MENU_RESTART-':
            # Die PIN-Abfrage vor dem Menue schuetzt auch den Neustart.
            if confirm_reboot() and not _reboot():
                popup('Neustart fehlgeschlagen.')
            continue
