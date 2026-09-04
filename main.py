import sys
import time
import signal
import json
import os
import math
import subprocess
import cv2
import PySimpleGUI as sg
import numpy as np
import transformlib as tl
import config_with_yaml as config
from camera import Camera
from datetime import datetime
from collections import deque

# Diagnose fuer die am 2026-08-28 beobachteten Faelle, in denen der Dienst
# beim Stoppen (z.B. "systemctl restart") nicht sofort reagierte und
# systemd nach TimeoutStopSec per SIGKILL eingreifen musste - das zaehlt
# systemd-seitig als eigener Fehlerzustand ("Failed with result 'timeout'")
# und loest OnFailure=/den Reboot-Guard aus, VOELLIG unabhaengig von der
# Staleness-Logik oben. Ohne diesen Handler war unklar, ob/wann main.py
# das SIGTERM ueberhaupt erreicht - jetzt landet das im (jetzt persistenten)
# Journal.
def _handle_sigterm(signum, frame):
    print("SIGTERM empfangen, beende main.py.", file=sys.stderr)
    sys.exit(0)

signal.signal(signal.SIGTERM, _handle_sigterm)

# Kein neuer Frame seit so vielen Sekunden -> Prozess beendet sich selbst
# (play_it/systemd uebernehmen den Neustart/die Eskalation, siehe README).
# camera.py haengt bei dauerhaftem Verbindungsverlust selbst nie (dessen
# eigene Retry-Schleife laeuft endlos weiter) - main.py braucht dieses
# eigene Signal, um ueberhaupt jemals aufzugeben.
STREAM_STALE_TIMEOUT_SEC = 10

# Eigene, grosszuegigere Gnadenfrist NUR fuer den allerersten Verbindungsaufbau
# nach dem App-Start (siehe camera.py::is_stale) - av.open() hat keinen
# expliziten Timeout, ein frischer Connect kann vereinzelt 30s+ dauern, ohne
# dass camera.py haengt. STREAM_STALE_TIMEOUT_SEC bleibt bewusst knapp fuer
# einen Ausfall WAEHREND eines bereits laufenden Streams.
STREAM_STARTUP_TIMEOUT_SEC = 30

version = '0.10.0'

cfg = config.load("config.yml")

last_image_id = 0

window = ''

# Ausschnitts-Konfiguration (section_full/section_detail) kann ueber den
# PIN-geschuetzten Settings-Screen live geaendert werden. Persistiert wird
# NICHT in config.yml (liegt auf dem Root-FS - bei aktivem Overlay wuerde
# eine Aenderung dort zwei Reboots brauchen, um dauerhaft zu wirken, siehe
# ansible/files/targetdisplay-save-sections.sh), sondern in dieser Datei auf
# der Boot-Partition, die live per remount beschreibbar gemacht werden kann.
# Existiert die Datei, gewinnen ihre Werte gegenueber config.yml.
SECTIONS_OVERRIDE_FILE = '/boot/firmware/targetdisplay-sections.json'

# Flottenweite Stand-Liste (Anzeigename + Kamera-URL je Stand), identisch
# per Ansible an jedes Geraet der Gruppe ausgerollt (siehe
# ansible/tasks/stands.yml) - welcher Stand ein KONKRETES Geraet zeigt,
# wird separat unten in ACTIVE_STAND_FILE festgehalten, einmalig ausgewaehlt
# im Ersteinrichtungs-Assistenten (siehe main()).
STANDS_FILE = '/boot/firmware/targetdisplay-stands.json'
ACTIVE_STAND_FILE = '/boot/firmware/targetdisplay-active-stand.json'

# Eingebauter Standard-PIN, falls weder config.yml noch PIN_FILE einen
# eigenen Wert setzen - main() erzwingt eine Aenderung im
# Ersteinrichtungs-Assistenten, solange der PIN noch auf diesem Wert steht
# (siehe change_pin_flow). Damit braucht eine Ansible-Installation KEINEN
# PIN mehr vorab zu setzen - passt zur "generisch, ohne Pro-Host-Variablen"
# Idee hinter der Stand-Liste oben.
DEFAULT_PIN = '1234'
PIN_FILE = '/boot/firmware/targetdisplay-pin.json'


def load_sections(cfg):
    # getPropertyWithDefault(..., None) statt getProperty(): in der neuen,
    # generischen Installation (mehrere Staende, siehe STANDS_FILE) hat
    # config.yml diese Felder gar nicht mehr - fehlen sie UND die
    # Override-Datei, ist das kein Fehler, sondern bedeutet "noch nicht
    # konfiguriert" (loest main()s Ersteinrichtungs-Assistenten aus).
    section_full = cfg.getPropertyWithDefault('video.section_full', None)
    section_detail = cfg.getPropertyWithDefault('video.section_detail', None)
    if os.path.exists(SECTIONS_OVERRIDE_FILE):
        try:
            with open(SECTIONS_OVERRIDE_FILE) as f:
                override = json.load(f)
            section_full = override.get('section_full', section_full)
            section_detail = override.get('section_detail', section_detail)
            print(f"Sections-Override aus {SECTIONS_OVERRIDE_FILE} geladen.", file=sys.stderr)
        except Exception as e:
            # Fail-soft ist hier bewusst: eine kaputte/unvollstaendige Datei
            # (z.B. durch einen Stromausfall waehrend des Schreibens) wird
            # wie "noch nicht konfiguriert" behandelt statt abzustuerzen -
            # main()s Assistent zeigt dann einfach den Schritt erneut.
            print(f"Konnte Sections-Override nicht lesen ({e}), nutze config.yml-Werte.", file=sys.stderr)
    return section_full, section_detail


def _run_save_script(script_name, payload):
    # Gemeinsame Aufruf-Logik fuer alle PIN-geschuetzten Speicher-Aktionen
    # (Ausschnitte, Stand-Auswahl, PIN) - jede Aktion hat trotzdem ihr
    # EIGENES, einzelnes Sudo-Skript (siehe ansible/tasks/settings_sudo.yml),
    # nur der main.py-seitige Aufruf-Code ist geteilt.
    try:
        result = subprocess.run(
            ['/usr/bin/sudo', f'/root/bin/{script_name}'],
            input=payload, text=True, capture_output=True, timeout=15
        )
        if result.returncode != 0:
            print(f"{script_name} fehlgeschlagen (Exit {result.returncode}): {result.stderr}", file=sys.stderr)
            return False
        return True
    except Exception as e:
        print(f"Konnte {script_name} nicht ausfuehren: {e}", file=sys.stderr)
        return False


def _points_to_json(points):
    return [[int(x), int(y)] for x, y in points] if points is not None else None


def save_sections_override(section_full, section_detail):
    # section_full/section_detail koennen einzeln None sein - waehrend des
    # Ersteinrichtungs-Assistenten wird zuerst nur "Ganze Scheibe"
    # gespeichert, "Innen Scheibe" ist zu diesem Zeitpunkt noch gar nicht
    # bekannt (siehe main()). None wird als JSON "null" mitgeschrieben,
    # load_sections() liest das ueber .get() korrekt wieder als "noch nicht
    # konfiguriert" ein - kein Sonderfall dort noetig.
    payload = json.dumps({
        'section_full': _points_to_json(section_full),
        'section_detail': _points_to_json(section_detail),
    })
    return _run_save_script('targetdisplay-save-sections.sh', payload)


def load_stands():
    # Fail-soft: fehlt/ist kaputt die Datei, liefert das eine leere Liste -
    # main()s Stand-Auswahl zeigt dann einen klaren Hinweis statt
    # abzustuerzen (z.B. wenn ein Ansible-Deploy die Datei noch nicht
    # gebracht hat).
    if not os.path.exists(STANDS_FILE):
        return []
    try:
        with open(STANDS_FILE) as f:
            data = json.load(f)
        return [s for s in data.get('stands', []) if s.get('id') and s.get('displayName') and s.get('url')]
    except Exception as e:
        print(f"Konnte {STANDS_FILE} nicht lesen ({e}), keine Stand-Liste verfuegbar.", file=sys.stderr)
        return []


def load_active_stand(stands):
    # Faellt zusaetzlich auf config.yml zurueck (einfacher Einzel-Stand-
    # Betrieb ohne die neue Mehr-Stand-Maschinerie, siehe config.yml.dist) -
    # das gilt aber NUR, solange es keine aktive Stand-Auswahl-Datei gibt;
    # existiert sie, hat sie Vorrang (konsistent mit load_sections).
    cfg_url = cfg.getPropertyWithDefault('video.url', None)
    cfg_name = cfg.getPropertyWithDefault('standName', None)
    fallback = {'id': None, 'displayName': cfg_name, 'url': cfg_url} if cfg_url else None

    if not os.path.exists(ACTIVE_STAND_FILE):
        return fallback
    try:
        with open(ACTIVE_STAND_FILE) as f:
            active = json.load(f)
        stand_id = active.get('id')
        for s in stands:
            if s['id'] == stand_id:
                return s
        # Kein Fehler: die ID zeigt auf einen Stand, der (noch) nicht in
        # STANDS_FILE steht - z.B. weil die Liste noch fehlt/unvollstaendig
        # ist. Wie "nicht ausgewaehlt" behandeln, main() zeigt dann erneut
        # die Stand-Auswahl statt mit einer falschen/leeren URL zu starten.
        print(f"Aktive Stand-ID '{stand_id}' nicht in {STANDS_FILE} gefunden, behandle als nicht ausgewaehlt.", file=sys.stderr)
        return fallback
    except Exception as e:
        print(f"Konnte {ACTIVE_STAND_FILE} nicht lesen ({e}), behandle als nicht ausgewaehlt.", file=sys.stderr)
        return fallback


def save_active_stand(stand_id):
    return _run_save_script('targetdisplay-save-active-stand.sh', json.dumps({'id': stand_id}))


def load_settings_pin():
    pin = cfg.getPropertyWithDefault('settingsPin', DEFAULT_PIN)
    if os.path.exists(PIN_FILE):
        try:
            with open(PIN_FILE) as f:
                override = json.load(f)
            pin = override.get('pin', pin)
        except Exception as e:
            print(f"Konnte {PIN_FILE} nicht lesen ({e}), nutze bisherigen PIN.", file=sys.stderr)
    return str(pin)


def save_pin(new_pin):
    return _run_save_script('targetdisplay-save-pin.sh', json.dumps({'pin': new_pin}))


# Feste Canvas-Groesse fuer den Punkte-Editor (edit_section_points): das
# Fenster/Layout wird nur EINMAL beim Programmstart gebaut (siehe
# main()/_show_page unten - PIN/Settings/Restart sind eigene "Seiten" im
# selben Fenster, kein separates sg.Window mehr), das tatsaechliche
# Kamerabild-Seitenverhaeltnis ist zu diesem Zeitpunkt aber noch nicht
# bekannt. Das Bild wird beim Zeichnen einfach oben links in dieser Flaeche
# platziert (siehe to_display_frame/redraw).
EDITOR_MAX_W, EDITOR_MAX_H = 1000, 620

# Alle "Seiten" des Kiosk-Fensters - _show_page blendet genau eine davon ein.
_PAGE_KEYS = ('-MAINVIEW-', '-PINVIEW-', '-CONFIRMVIEW-', '-MENUVIEW-', '-EDITORVIEW-',
              '-STANDVIEW-', '-CAMWAITVIEW-')

# Zusaetzliche, grobe Zeichenbegrenzung fuer den Standnamen - der eigentliche
# Schutz gegen ein auseinandergedruecktes Layout ist der feste Pixel-Rahmen
# um das Text-Element in main() (sg.Frame(size=...), erzwingt per
# pack_propagate(0) eine harte Breite). Diese Kuerzung hier ist nur eine
# zusaetzliche Sicherheitsmarge, damit gar nicht erst extrem lange Strings
# an Tk uebergeben werden.
STANDNAME_MAX_CHARS = 30


def _set_stand_name(name):
    name = name or ''
    if len(name) > STANDNAME_MAX_CHARS:
        name = name[:STANDNAME_MAX_CHARS - 1] + '…'
    window['-STANDNAME-'].update(name)


def _show_page(key):
    # EIN Fenster mit mehreren Seiten statt separater sg.Window()-Dialoge:
    # matchbox-window-manager hat sich bei mehreren gleichzeitig existierenden
    # Toplevel-Fenstern live am Test-Pi als nicht robust erwiesen (fixe/nicht
    # verhandelbare Platzierung fuer no_titlebar-Fenster ueber den X11-
    # Fenstertyp "dock", dazu wiederholte BadWindow/BadDrawable-X-Fehler im
    # eigenen matchbox-Log) - ein zweites Toplevel-Fenster war schlicht nicht
    # zuverlaessig zu positionieren. Eine eigene "Seite" im selben, bereits
    # korrekt (0,0, volle Screengroesse) platzierten Hauptfenster umgeht das
    # Problem komplett.
    for k in _PAGE_KEYS:
        window[k].update(visible=(k == key))
    # Ohne refresh() wird die neu sichtbare Seite oft erst beim naechsten
    # Tk-Redraw-Zyklus tatsaechlich gezeichnet - das nachfolgende
    # blockierende window.read() liefert aber keinen Anlass dafuer von
    # selbst (live am Test-Pi beobachtet: Bildschirm blieb bis zum ersten
    # Klick/Timeout komplett leer).
    window.refresh()


def check_pin(correct_pin):
    # Generische PIN-Abfrage, schuetzt sowohl Settings als auch Restart.
    # Bewusst keine Sperre nach Fehlversuchen (Nutzer-Entscheidung) - das
    # Bedrohungsmodell ist "zufaelliges Herumtippen vor Ort abschrecken",
    # keine gezielte Brute-Force-Absicherung.
    # Titel/Abbrechen-Sichtbarkeit explizit zuruecksetzen - _enter_new_pin()
    # (PIN-Aenderung) aendert beides auf derselben Seite, eine vorherige
    # Aenderung darf hier nicht durchschlagen.
    window['-PIN_TITLE-'].update('PIN eingeben')
    window['-PIN_CANCEL-'].update(visible=True)
    _show_page('-PINVIEW-')
    window['-PINDISPLAY-'].update('', text_color='black')
    entered = ''
    result = False
    while True:
        event, _ = window.read()
        if event in (sg.WIN_CLOSED, '-PIN_CANCEL-'):
            break
        elif event == '-PIN_CLEAR-':
            entered = ''
        elif event == '-PIN_OK-':
            if entered == str(correct_pin):
                result = True
                break
            else:
                entered = ''
                window['-PINDISPLAY-'].update('falsch', text_color='red')
                window.refresh()
                time.sleep(0.6)
        elif event in '0123456789':
            if len(entered) < 6:
                entered += event
        window['-PINDISPLAY-'].update('*' * len(entered), text_color='black')
    _show_page('-MAINVIEW-')
    return result


def confirm_reboot():
    _show_page('-CONFIRMVIEW-')
    event, _ = window.read()
    _show_page('-MAINVIEW-')
    return event == '-CONFIRM_YES-'


def _enter_new_pin(title, show_cancel):
    # Tastatur-Grundgeruest wie check_pin(), aber OHNE Vergleich mit einem
    # "richtigen" PIN - liefert die eingetippte Ziffernfolge (4-6 Stellen,
    # OK gedrueckt) zurueck, oder None (Abbrechen/Fenster zu). Wird sowohl
    # fuer die Neueingabe als auch die Wiederholung genutzt (change_pin_flow
    # ruft diese Funktion zweimal auf).
    window['-PIN_TITLE-'].update(title)
    window['-PIN_CANCEL-'].update(visible=show_cancel)
    _show_page('-PINVIEW-')
    window['-PINDISPLAY-'].update('', text_color='black')
    entered = ''
    result = None
    while True:
        event, _ = window.read()
        if event in (sg.WIN_CLOSED, '-PIN_CANCEL-'):
            break
        elif event == '-PIN_CLEAR-':
            entered = ''
        elif event == '-PIN_OK-':
            if 4 <= len(entered) <= 6:
                result = entered
                break
            else:
                window['-PINDISPLAY-'].update('4-6 Ziffern', text_color='red')
                window.refresh()
                time.sleep(0.8)
                entered = ''
        elif event in '0123456789':
            if len(entered) < 6:
                entered += event
        window['-PINDISPLAY-'].update('*' * len(entered), text_color='black')
    # Seite fuer die naechste Nutzung (check_pin) wieder in den
    # Grundzustand versetzen.
    window['-PIN_TITLE-'].update('PIN eingeben')
    window['-PIN_CANCEL-'].update(visible=True)
    return result


def change_pin_flow(current_pin, forced):
    # forced=True: Ersteinrichtungs-Assistent, solange der PIN noch auf dem
    # Standardwert DEFAULT_PIN steht - kein Abbrechen moeglich (Cancel-
    # Button versteckt), es gibt ja noch nichts zu schuetzen. forced=False:
    # freiwillige Aenderung ueber das Settings-Menue - verlangt zuerst den
    # AKTUELLEN PIN zur Bestaetigung (wie ein normaler "Passwort aendern"-
    # Dialog), abbrechbar.
    if not forced:
        if not check_pin(current_pin):
            return None
    while True:
        new1 = _enter_new_pin('Neuen PIN eingeben (4-6 Ziffern)', show_cancel=not forced)
        if new1 is None:
            return None
        new2 = _enter_new_pin('PIN wiederholen', show_cancel=not forced)
        if new2 is None:
            return None
        if new1 == new2:
            break
        window['-PIN_TITLE-'].update('PINs stimmen nicht überein')
        window.refresh()
        time.sleep(1.0)
    if save_pin(new1):
        return new1
    sg.popup('PIN konnte nicht gespeichert werden.', keep_on_top=True)
    return None


def run_stand_select(stands, forced):
    # forced=True: Ersteinrichtungs-Assistent, noch kein Stand ausgewaehlt -
    # kein Zurueck moeglich (der Hauptbildschirm ist ja noch nicht
    # erreichbar). forced=False: freiwilliger Stand-Wechsel ueber das
    # Settings-Menue, abbrechbar.
    _show_page('-STANDVIEW-')
    window['-STAND_BACK-'].update(visible=not forced)
    if not stands:
        # Keine Stand-Liste vorhanden (z.B. Ansible-Deploy hat sie noch
        # nicht gebracht) - klare Meldung statt einer leeren, ratlosen
        # Auswahlliste.
        window['-STAND_LIST-'].update(values=['(keine Stand-Liste gefunden - bitte Installation prüfen)'])
        window['-STAND_SELECT-'].update(disabled=True)
    else:
        window['-STAND_LIST-'].update(values=[s['displayName'] for s in stands])
        window['-STAND_SELECT-'].update(disabled=False)
    window.refresh()
    while True:
        event, values = window.read()
        if event == sg.WIN_CLOSED:
            return None
        elif event == '-STAND_BACK-' and not forced:
            return None
        elif event == '-STAND_SELECT-' and stands:
            # curselection() statt Werteabgleich per Name - robust auch
            # falls zwei Staende zufaellig denselben Anzeigenamen haben.
            sel = window['-STAND_LIST-'].Widget.curselection()
            if sel:
                return stands[sel[0]]


def _wait_for_camera_frame(cap, max_wait_sec=STREAM_STARTUP_TIMEOUT_SEC):
    # Gnadenfrist fuer den allerersten Frame direkt nach der Stand-Auswahl -
    # dieselbe Grosszuegigkeit wie STREAM_STARTUP_TIMEOUT_SEC (av.open() hat
    # keinen expliziten Verbindungs-Timeout, ein frischer Connect kann
    # vereinzelt 30s+ dauern, siehe Camera.is_stale-Kommentar; live am
    # Test-Pi hat allein der Container-Open schon 8s gebraucht). Anders als
    # ein erster Entwurf (blockierendes time.sleep() bis zum Ablauf, DANACH
    # erst eine interaktive Seite) pollt diese Version durchgehend ueber
    # window.read(timeout=...) - das Fenster bleibt die ganze Wartezeit über
    # reaktionsfaehig (Zurueck-Knopf funktioniert sofort, kein bis zu 30s
    # eingefroren wirkender Bildschirm) UND erkennt eine zwischenzeitlich
    # doch noch erfolgreiche Verbindung automatisch, ganz ohne Klick auf
    # "Erneut versuchen". Gibt den Frame zurueck, oder den String 'back'
    # wenn der Nutzer zur Stand-Auswahl zurueck moechte.
    _show_page('-CAMWAITVIEW-')
    window['-CAMWAIT_TEXT-'].update('Verbinde mit Kamera...')
    window.refresh()
    deadline = time.time() + max_wait_sec
    timed_out = False
    while True:
        frame = cap.getFrame(full=True)
        if frame is not None:
            return frame
        if not timed_out and time.time() >= deadline:
            timed_out = True
            window['-CAMWAIT_TEXT-'].update('Kamera nicht erreichbar.\nBitte URL/Verkabelung prüfen.')
            window.refresh()
        event, _ = window.read(timeout=300)
        if event in (sg.WIN_CLOSED, '-CAMWAIT_BACK-'):
            return 'back'


def edit_section_points(region_label, cap, points, other_points=None, allow_cancel=True):
    # points: Liste von 4 [x,y] in ORIGINALEN Kamerakoordinaten (nicht
    # Crop-verschoben), oder None falls noch keine Kalibrierung existiert
    # (Ersteinrichtungs-Assistent) - dann wird unten ein zentriertes
    # Rechteck als Startwert gesetzt, von dem aus die Ecken manuell in
    # Position gezogen werden. cap.getFrame(full=True) liefert bewusst das
    # unbeschnittene Kamerabild (siehe camera.py) - der normale Anzeige-Crop
    # ist eng um die AKTUELLEN section_full/section_detail-Grenzen gelegt,
    # der Editor zum NEU-Setzen der Ausschnitte muss aber auch Bereiche
    # ausserhalb dieser Grenzen zeigen koennen. Gibt die neue Punkteliste
    # (gleicher, originaler Koordinatenraum) beim Speichern zurueck, sonst None.
    frame = cap.getFrame(full=True)
    if frame is None:
        sg.popup('Kein Kamerabild verfügbar - bitte später erneut versuchen.', keep_on_top=True)
        return None

    img_h, img_w = frame.shape[:2]
    if points is None:
        mx, my = int(img_w * 0.2), int(img_h * 0.2)
        points = [[mx, my], [img_w - mx, my], [mx, img_h - my], [img_w - mx, img_h - my]]
    max_disp_w, max_disp_h = EDITOR_MAX_W, EDITOR_MAX_H
    scale = min(max_disp_w / img_w, max_disp_h / img_h, 1.0)
    disp_w, disp_h = max(1, int(img_w * scale)), max(1, int(img_h * scale))
    # Der Graph-Canvas hat eine feste Groesse (EDITOR_MAX_W x EDITOR_MAX_H -
    # das Layout wird nur einmal beim Programmstart gebaut, siehe
    # _show_page-Kommentar), das tatsaechliche Kamerabild passt je nach
    # Seitenverhaeltnis meist nicht exakt hinein. off_x/off_y zentrieren das
    # skalierte Bild in diesem Canvas, statt es oben links kleben zu lassen
    # (das erzeugte vorher einen einseitigen schwarzen Rand rechts).
    off_x, off_y = (EDITOR_MAX_W - disp_w) // 2, (EDITOR_MAX_H - disp_h) // 2

    HIT_RADIUS = 35   # grosszuegiger Trefferbereich fuer Finger, in Display-Pixeln
    MAG_SIZE = 220    # Seitenlaenge des Lupen-Overlays in Pixeln
    MAG_SRC = 70       # Seitenlaenge des vergroesserten Kamera-Ausschnitts (Quelle)

    def to_display_frame(f):
        return cv2.resize(f, (disp_w, disp_h)) if scale != 1.0 else f.copy()

    disp_frame = to_display_frame(frame)
    # pts leben ab hier durchgehend in Canvas-Koordinaten (Bild-Skalierung
    # UND Zentrierungs-Offset bereits eingerechnet) - das entspricht direkt
    # dem Koordinatenraum, den PySimpleGUI fuer Graph-Klicks liefert, dadurch
    # ist beim Dragging keine weitere Umrechnung noetig.
    pts = [[p[0] * scale + off_x, p[1] * scale + off_y] for p in points]
    # Der jeweils ANDERE Ausschnitt (z.B. section_detail waehrend
    # section_full bearbeitet wird) wird nur informativ in hellgrau
    # mitgezeichnet, damit man beim Setzen der Punkte sieht wie sich beide
    # Bereiche zueinander verhalten - rein statisch, nicht klickbar/ziehbar.
    other_pts = None
    if other_points is not None:
        other_pts = [[p[0] * scale + off_x, p[1] * scale + off_y] for p in other_points]

    _show_page('-EDITORVIEW-')
    window['-EDITOR_TITLE-'].update(f'{region_label}: Eckpunkte anpassen')
    window['-EDIT_CANCEL-'].update(visible=allow_cancel)
    graph = window['-EDITGRAPH-']

    def draw_magnifier(center_disp):
        # Punkt kann bis an den Bildrand/in die Ecke gezogen werden - ein
        # einfaches Clamping des Quellausschnitts wuerde dort ein
        # asymmetrisches, verzerrtes Rechteck ergeben (cv2.resize wuerde es
        # verzerrt auf ein Quadrat aufziehen) UND das Fadenkreuz saesse
        # nicht mehr auf dem tatsaechlichen Punkt. Stattdessen wird der
        # fehlende Rand per BORDER_REPLICATE aufgefuellt, der Ausschnitt
        # bleibt so immer MAG_SRC x MAG_SRC und zentriert auf dem Punkt.
        cx, cy = (center_disp[0] - off_x) / scale, (center_disp[1] - off_y) / scale
        half = MAG_SRC // 2
        x0, y0 = int(cx - half), int(cy - half)
        x1, y1 = x0 + MAG_SRC, y0 + MAG_SRC
        src_x0, src_y0 = max(0, x0), max(0, y0)
        src_x1, src_y1 = min(img_w, x1), min(img_h, y1)
        crop = frame[src_y0:src_y1, src_x0:src_x1]
        if crop.size == 0:
            return
        pad_left, pad_top = src_x0 - x0, src_y0 - y0
        pad_right, pad_bottom = x1 - src_x1, y1 - src_y1
        if pad_left or pad_top or pad_right or pad_bottom:
            crop = cv2.copyMakeBorder(crop, pad_top, pad_bottom, pad_left, pad_right, cv2.BORDER_REPLICATE)
        mag = cv2.resize(crop, (MAG_SIZE, MAG_SIZE), interpolation=cv2.INTER_NEAREST)
        cv2.line(mag, (MAG_SIZE // 2, 0), (MAG_SIZE // 2, MAG_SIZE), (0, 255, 255), 1)
        cv2.line(mag, (0, MAG_SIZE // 2), (MAG_SIZE, MAG_SIZE // 2), (0, 255, 255), 1)
        magbytes = cv2.imencode('.png', mag)[1].tobytes()
        # Fest oben rechts im Canvas verankert (nicht am Bild), damit die
        # Lupe unabhaengig von Bildgroesse/Zentrierung immer an derselben,
        # vorhersehbaren Stelle erscheint.
        mag_x, mag_y = EDITOR_MAX_W - MAG_SIZE - 10, 10
        graph.draw_image(data=magbytes, location=(mag_x, mag_y))
        graph.draw_rectangle((mag_x, mag_y), (mag_x + MAG_SIZE, mag_y + MAG_SIZE), line_color='yellow', line_width=2)

    def draw_outline(poly_pts, color):
        # Die Reihenfolge der Punkte in section_full/section_detail folgt
        # keiner festen Umlauf-Konvention (nicht zwingend im/gegen den
        # Uhrzeigersinn) - ein direktes Verbinden 1-2-3-4-1 in dieser
        # Reihenfolge kann daher ein sich selbst ueberschneidendes Viereck
        # ("Bowtie") ergeben, live am Test-Pi beobachtet. Fuer den
        # Verbindungs-Umriss werden die Punkte deshalb separat nach Winkel
        # um ihren Mittelpunkt sortiert (reiner Anzeige-Zweck) - die
        # Nummerierung/Zuordnung 1-4 der Punkte selbst bleibt unveraendert.
        cx = sum(p[0] for p in poly_pts) / 4
        cy = sum(p[1] for p in poly_pts) / 4
        perimeter = sorted(range(4), key=lambda i: math.atan2(poly_pts[i][1] - cy, poly_pts[i][0] - cx))
        for j in range(4):
            graph.draw_line(poly_pts[perimeter[j]], poly_pts[perimeter[(j + 1) % 4]], color=color, width=2)

    def redraw(mag_center=None):
        graph.erase()
        imgbytes = cv2.imencode('.png', disp_frame)[1].tobytes()
        graph.draw_image(data=imgbytes, location=(off_x, off_y))
        if other_pts is not None:
            draw_outline(other_pts, '#c0c0c0')
        draw_outline(pts, 'yellow')
        for i, p in enumerate(pts):
            graph.draw_circle(p, 10, fill_color='red', line_color='yellow', line_width=2)
            graph.draw_text(str(i + 1), (p[0], p[1] - 20), color='yellow', font=('Helvetica', 12, 'bold'))
        if mag_center is not None:
            draw_magnifier(mag_center)

    redraw()
    dragging_idx = None

    while True:
        event, values = window.read()
        if event in (sg.WIN_CLOSED, '-EDIT_CANCEL-'):
            return None
        elif event == '-EDIT_REFRESH-':
            new_frame = cap.getFrame(full=True)
            if new_frame is not None:
                frame = new_frame
                disp_frame = to_display_frame(frame)
                redraw()
        elif event == '-EDIT_SAVE-':
            return [[(p[0] - off_x) / scale, (p[1] - off_y) / scale] for p in pts]
        elif event == '-EDITGRAPH-':
            pos = values['-EDITGRAPH-']
            if pos is None:
                continue
            if dragging_idx is None:
                best_i, best_d = None, HIT_RADIUS
                for i, p in enumerate(pts):
                    d = ((p[0] - pos[0]) ** 2 + (p[1] - pos[1]) ** 2) ** 0.5
                    if d < best_d:
                        best_i, best_d = i, d
                dragging_idx = best_i
            if dragging_idx is not None:
                # Auf den tatsaechlich sichtbaren Bildbereich begrenzt (nicht
                # den ganzen Canvas) - ausserhalb liegt nur der zentrierte
                # schwarze Rand, ohne zugehoerige Bildkoordinate.
                pts[dragging_idx] = [
                    min(max(pos[0], off_x), off_x + disp_w),
                    min(max(pos[1], off_y), off_y + disp_h),
                ]
                redraw(mag_center=pts[dragging_idx])
        elif event == '-EDITGRAPH-+UP':
            dragging_idx = None
            redraw()


def run_settings_flow(cap, section_full, section_detail, stands, current_pin):
    # Fuehrt Auswahl + jeweilige Aktion durch. section_full/section_detail
    # sind die ORIGINALEN Koordinaten, wie sie aus config.yml/Override
    # kommen und auch wieder dorthin geschrieben werden -
    # edit_section_points() arbeitet jetzt direkt in diesem Koordinatenraum
    # (zeigt via cap.getFrame(full=True) das unbeschnittene Kamerabild,
    # keine Crop-Offset-Umrechnung mehr noetig, siehe dort). Gibt True
    # zurueck wenn irgendetwas gespeichert wurde, das einen neu berechneten
    # Zustand braucht - main.py beendet sich dann (sys.exit(0)),
    # Restart=always im systemd-Unit laedt alles sauber neu (siehe
    # Aufrufer). Gilt einheitlich fuer Ausschnitte, Stand-Wechsel UND
    # PIN-Aenderung - bewusst kein Sonderfall fuer den PIN, der zwar
    # technisch keinen Neustart braeuchte, aber ein einheitlicher Ablauf
    # ist weniger fehleranfaellig als eine zweite, live-aktualisierte
    # Variable an mehreren Stellen mitzupflegen.
    _show_page('-MENUVIEW-')
    which = None
    while True:
        event, _ = window.read()
        if event in (sg.WIN_CLOSED, '-MENU_BACK-'):
            _show_page('-MAINVIEW-')
            return False
        elif event in ('-MENU_FULL-', '-MENU_DETAIL-'):
            which = 'full' if event == '-MENU_FULL-' else 'detail'
            break
        elif event == '-MENU_STAND-':
            chosen = run_stand_select(stands, forced=False)
            _show_page('-MAINVIEW-')
            if chosen is None:
                return False
            if save_active_stand(chosen['id']):
                return True
            sg.popup('Stand konnte nicht gespeichert werden.', keep_on_top=True)
            return False
        elif event == '-MENU_PIN-':
            new_pin = change_pin_flow(current_pin, forced=False)
            _show_page('-MAINVIEW-')
            return new_pin is not None

    current = section_full if which == 'full' else section_detail
    other = section_detail if which == 'full' else section_full

    result = edit_section_points('Ganze Scheibe' if which == 'full' else 'Innen Scheibe', cap, current, other_points=other)
    _show_page('-MAINVIEW-')
    if result is None:
        return False

    new_section_full = result if which == 'full' else section_full
    new_section_detail = result if which == 'detail' else section_detail

    if not save_sections_override(new_section_full, new_section_detail):
        sg.popup('Speichern fehlgeschlagen - Änderung wurde NICHT übernommen.', keep_on_top=True)
        return False
    return True

def zoom_disabled(disable):
  window['-FULL_VIDEO-'].update(disabled=disable)
  window['-DETAIL_VIDEO-'].update(disabled=disable)
  window['-RESETZOOM-'].update(disabled=True)

def blink_disabled(disable):
  window['-BLINK_START-'].update(disabled=disable)
  window['-BLINK_STOP-'].update(disabled=True)
  window['-BLINK_REF-'].update(disabled=True)

def timer_disabled(disable):
  window['-TIMER_5_3_7-'].update(disabled=disable)
  window['-TIMER_20-'].update(disabled=disable)
  window['-TIMER_10-'].update(disabled=disable)
  window['-TIMER_STOP-'].update(disabled=True)

def video_filter_disabled(disable):
  window['-TOGGLEVIDEO-'].update(disabled=disable)

def blend_logo_centered(canvas, logo_rgba, margin_ratio=0.05):
    # skaliert ein BGRA-Logo unter Beibehaltung des Seitenverhaeltnisses so
    # gross wie moeglich (minus Rand) und zeichnet es alpha-transparent
    # zentriert auf den Canvas
    canvas_h, canvas_w = canvas.shape[:2]
    margin = int(min(canvas_w, canvas_h) * margin_ratio)
    max_w = canvas_w - 2 * margin
    max_h = canvas_h - 2 * margin
    logo_h, logo_w = logo_rgba.shape[:2]
    scale = min(max_w / logo_w, max_h / logo_h)
    new_w, new_h = int(logo_w * scale), int(logo_h * scale)
    logo_rgba = cv2.resize(logo_rgba, (new_w, new_h))
    x0 = (canvas_w - new_w) // 2
    y0 = (canvas_h - new_h) // 2
    if logo_rgba.shape[2] == 4:
        alpha = logo_rgba[:, :, 3:4].astype(np.float32) / 255.0
        logo_bgr = logo_rgba[:, :, :3].astype(np.float32)
        roi = canvas[y0:y0+new_h, x0:x0+new_w].astype(np.float32)
        canvas[y0:y0+new_h, x0:x0+new_w] = (alpha * logo_bgr + (1 - alpha) * roi).astype(np.uint8)
    else:
        canvas[y0:y0+new_h, x0:x0+new_w] = logo_rgba[:, :, :3]
    return canvas

def draw_image(window_video, frame):
    global last_image_id
    # PPM statt PNG: DrawImage() reicht die Bytes nur an tk.PhotoImage(data=...)
    # durch (siehe PySimpleGUI-Quelltext), das kann PPM nativ genauso wie PNG.
    # PNG ist verlustfrei KOMPRIMIERT (DEFLATE) - bei jedem einzelnen Frame neu
    # zu komprimieren kostet spuerbar CPU, ohne dass die Kompression hier
    # irgendeinen Nutzen haette (das Ergebnis wird sofort wieder dekodiert,
    # nie gespeichert/uebertragen). PPM ist unkomprimiert (groesserer
    # Byte-Blob, aber rein lokale In-Prozess-Uebergabe an Tcl/Tk, keine
    # Netzwerkuebertragung) und dadurch beim Kodieren deutlich billiger.
    imgbytes = cv2.imencode('.ppm', frame)[1].tobytes()
    actual_image_id = window_video.DrawImage(data=imgbytes,location=(0,0))
    if last_image_id: window_video.delete_figure(last_image_id)
    last_image_id = actual_image_id


def main():
    VideoSize = (cfg.getProperty('video.size.x'), cfg.getProperty('video.size.y'))

    frame_count = 1
    startupTime = datetime.now()
    frame_timestamps = deque()
    FPS_WINDOW_SECONDS = 60
    displayVideo = True
    displayTimer = False
    timerLoop = 0
    timerCurrentLoop = 0
    timerStart = datetime.now()
    timerType = ""
    blink = False
    blink_ref = []
    zoom_center = []
    zoom_level = 'full'
    last_frame_id = -1
    global window

    sg.theme('LightGreen')

    left_col = [
      # sg.Text(size=...) allein reicht NICHT: das ist bei Tk nur eine
      # Mindestbreite in Zeichen, kein Maximum - bei fetter/grosser Schrift
      # (25pt bold) wird das Element trotzdem breiter als size= wenn der
      # Inhalt es verlangt. Der Standname kommt seit der Mehr-Stand-
      # Faehigkeit aus der frei editierbaren targetdisplay-stands.json,
      # nicht mehr aus einer kurzen, kontrollierten Ansible-Variable - ein
      # zu langer Name hat live am Test-Pi das ganze Layout auseinander-
      # gedrueckt und dadurch das Videobild verschoben/verkleinert. Ein
      # sg.Frame mit size=(Pixel, Pixel) erzwingt dagegen ueber
      # pack_propagate(0) eine wirklich harte Breite - ueberstehender
      # Inhalt wird abgeschnitten statt den Frame zu vergroessern.
      [sg.Frame('', [[sg.Text('', key='-STANDNAME-', font=('Helvetica', 25, 'underline bold'))]],
                size=(440, 45), border_width=0, pad=(0, 0))],

      [sg.Frame('Zoom',[[sg.Button('Ganze Scheibe',key='-FULL_VIDEO-', size=(13, 2)),sg.Button('Innen Scheibe', key='-DETAIL_VIDEO-', size=(13, 2)),sg.Button('Reset', key='-RESETZOOM-', disabled=True, size=(13,2))]],)],
      [sg.Frame('Blinken',[[sg.Button('Start',key='-BLINK_START-', size=(13, 2)),sg.Button('Referenz', key='-BLINK_REF-', size=(13, 2), disabled=True),sg.Button('Stop', key='-BLINK_STOP-', size=(13,2), disabled=True)]],)],
      [sg.Frame('Timer',[
        [sg.Button('5 x 3/7 Sek.',key='-TIMER_5_3_7-', size=(13, 2)),sg.Button('20 Sek.', key='-TIMER_20-', size=(13, 2)),sg.Button('10 Sek.', key='-TIMER_10-', size=(13, 2))],
        [sg.Button('Stop', key='-TIMER_STOP-', size=(13,2), disabled=True, expand_x=True)]
      ])],
      [sg.HorizontalSeparator(pad=(0, (10, 10)))],
      [sg.Frame('', [[
        sg.Button('Video aus', key='-TOGGLEVIDEO-', size=(13, 2)),
        sg.Button('Settings (PIN)', key='-SETTINGS-', size=(13, 2)),
        sg.Button('Restart (PIN)', key='-RESTART-', size=(13, 2)),
      ]], border_width=0)],
      [sg.VPush()],
      [sg.Image(filename='', key='-LOGO-'), sg.Push(), sg.Frame('Datum / Uhrzeit',[
        [sg.Column([
          [sg.Text(key="-DATE-", font=('Courier', 14))],
          [sg.Text(key="-TIME-", font=('Courier', 34, 'bold'))]
        ], element_justification='right', pad=(15,5))]
      ])],
      [sg.Text("V: " + version, font=('Helvetica',8), pad=((5,5),(0,15))), sg.Text(key = '-FPS-',size=(20, 1),font=('Helvetica',8), pad=((5,5),(0,15)))]
    ]

    # PIN-Eingabe, Restart-Bestaetigung, Settings-Regionauswahl und der
    # Punkte-Editor sind eigene "Seiten" im selben Fenster (siehe
    # _show_page) statt separater sg.Window()-Dialoge - matchbox-window-
    # manager hat sich fuer ein zweites Toplevel-Fenster live am Test-Pi als
    # nicht robust erwiesen (siehe _show_page-Kommentar).
    main_view = sg.Column([
      [sg.Column(left_col, expand_y=True),sg.Graph(canvas_size=VideoSize, graph_bottom_left=(0,100), graph_top_right=(100,0), key='-VIDEO-', background_color='black', enable_events=True)]
    ], key='-MAINVIEW-', visible=True)

    # WICHTIG: alle vier Seiten hier bewusst OHNE visible=False anlegen -
    # PySimpleGUI/Tk hat sich live am Test-Pi als nicht zuverlaessig
    # erwiesen, wenn eine Column gleich bei der Erstellung visible=False
    # bekommt und erst SPAETER per .update(visible=True) eingeblendet wird
    # (blieb dauerhaft leer, obwohl kein Fehler geworfen wurde). Stattdessen
    # werden alle Seiten sichtbar erzeugt und direkt nach window.finalize()
    # bis auf -MAINVIEW- wieder ausgeblendet (siehe main() weiter unten) -
    # das entspricht dem "erst sichtbar machen, dann verstecken"-Vorgehen,
    # das bei PySimpleGUI zuverlaessig funktioniert.
    pin_frame = sg.Frame('', [
      [sg.Text('PIN eingeben', key='-PIN_TITLE-', font=('Helvetica', 30))],
      [sg.Text('', key='-PINDISPLAY-', font=('Courier', 40), size=(10, 1), justification='center')],
      [sg.Button('1', size=(8, 4)), sg.Button('2', size=(8, 4)), sg.Button('3', size=(8, 4))],
      [sg.Button('4', size=(8, 4)), sg.Button('5', size=(8, 4)), sg.Button('6', size=(8, 4))],
      [sg.Button('7', size=(8, 4)), sg.Button('8', size=(8, 4)), sg.Button('9', size=(8, 4))],
      [sg.Button('Löschen', key='-PIN_CLEAR-', size=(8, 4)), sg.Button('0', size=(8, 4)), sg.Button('OK', key='-PIN_OK-', size=(8, 4))],
      [sg.Button('Abbrechen', key='-PIN_CANCEL-', size=(26, 2))],
    ], element_justification='center', border_width=2, pad=(30, 30))

    # VPush/Push zum Zentrieren: funktioniert hier zuverlaessig, weil diese
    # Column ein Geschwister-Element in derselben Fensterzeile wie
    # -MAINVIEW- ist (siehe layout weiter unten) und dadurch ihren vollen
    # Anteil an Fensterbreite/-hoehe bekommt - als eigene, gestapelte Zeile
    # unter -MAINVIEW- (fruehere Variante) blieb dafuer schlicht kein Platz
    # und VPush/Push griffen ins Leere.
    pin_view = sg.Column([
      [sg.VPush()],
      [sg.Push(), pin_frame, sg.Push()],
      [sg.VPush()],
    ], key='-PINVIEW-', expand_x=True, expand_y=True)

    confirm_frame = sg.Frame('', [
      [sg.Text('Gerät jetzt neu starten?', font=('Helvetica', 28))],
      [sg.Button('Ja, neu starten', key='-CONFIRM_YES-', size=(20, 3)), sg.Button('Abbrechen', key='-CONFIRM_NO-', size=(20, 3))],
    ], element_justification='center', border_width=2, pad=(30, 30))

    confirm_view = sg.Column([
      [sg.VPush()],
      [sg.Push(), confirm_frame, sg.Push()],
      [sg.VPush()],
    ], key='-CONFIRMVIEW-', expand_x=True, expand_y=True)

    menu_frame = sg.Frame('', [
      [sg.Text('Einstellungen', font=('Helvetica', 24))],
      [sg.Button('Ganze Scheibe', key='-MENU_FULL-', size=(24, 3))],
      [sg.Button('Innen Scheibe', key='-MENU_DETAIL-', size=(24, 3))],
      [sg.Button('Stand wechseln', key='-MENU_STAND-', size=(24, 3))],
      [sg.Button('PIN ändern', key='-MENU_PIN-', size=(24, 3))],
      [sg.Button('Zurück', key='-MENU_BACK-', size=(24, 2))],
    ], element_justification='center', border_width=2, pad=(30, 30))

    # Weitere Settings-Punkte kommen vermutlich noch dazu - menu_view bleibt
    # deshalb bewusst eine eigene, generische Auswahlseite statt fest mit
    # nur zwei Optionen verdrahtet zu sein.
    menu_view = sg.Column([
      [sg.VPush()],
      [sg.Push(), menu_frame, sg.Push()],
      [sg.VPush()],
    ], key='-MENUVIEW-', expand_x=True, expand_y=True)

    editor_frame = sg.Frame('', [
      [sg.Text('', key='-EDITOR_TITLE-', font=('Helvetica', 18))],
      [sg.Graph(canvas_size=(EDITOR_MAX_W, EDITOR_MAX_H), graph_bottom_left=(0, EDITOR_MAX_H), graph_top_right=(EDITOR_MAX_W, 0),
                key='-EDITGRAPH-', enable_events=True, drag_submits=True, background_color='black')],
      [sg.Button('Neues Bild', key='-EDIT_REFRESH-', size=(13, 2)),
       sg.Button('Speichern', key='-EDIT_SAVE-', size=(13, 2)),
       sg.Button('Abbrechen', key='-EDIT_CANCEL-', size=(13, 2))],
    ], element_justification='center', border_width=2, pad=(15, 15))

    editor_view = sg.Column([
      [sg.VPush()],
      [sg.Push(), editor_frame, sg.Push()],
      [sg.VPush()],
    ], key='-EDITORVIEW-', expand_x=True, expand_y=True)

    stand_frame = sg.Frame('', [
      [sg.Text('Stand auswählen', font=('Helvetica', 28))],
      [sg.Listbox(values=[], size=(38, 8), font=('Helvetica', 20), key='-STAND_LIST-')],
      [sg.Button('Auswählen', key='-STAND_SELECT-', size=(20, 2)),
       sg.Button('Zurück', key='-STAND_BACK-', size=(20, 2))],
    ], element_justification='center', border_width=2, pad=(30, 30))

    stand_view = sg.Column([
      [sg.VPush()],
      [sg.Push(), stand_frame, sg.Push()],
      [sg.VPush()],
    ], key='-STANDVIEW-', expand_x=True, expand_y=True)

    camwait_frame = sg.Frame('', [
      [sg.Text('', key='-CAMWAIT_TEXT-', font=('Helvetica', 22), size=(40, 3), justification='center')],
      [sg.Button('Erneut versuchen', key='-CAMWAIT_RETRY-', size=(20, 2)),
       sg.Button('Zurück zur Stand-Auswahl', key='-CAMWAIT_BACK-', size=(24, 2))],
    ], element_justification='center', border_width=2, pad=(30, 30))

    camwait_view = sg.Column([
      [sg.VPush()],
      [sg.Push(), camwait_frame, sg.Push()],
      [sg.VPush()],
    ], key='-CAMWAITVIEW-', expand_x=True, expand_y=True)

    # Alle Seiten MUESSEN in derselben Zeile stehen (Geschwister-Elemente),
    # nicht als eigene Zeilen untereinander: main_view fuellt bei diesem
    # fixed-size-Fenster bereits die komplette Hoehe, darunter gestapelte
    # Zeilen haetten schlicht keinen Platz mehr und wuerden unsichtbar
    # bleiben, egal ob sie visible=True/False sind (live am Test-Pi
    # verifiziert). In einer gemeinsamen Zeile "faltet" PySimpleGUI
    # unsichtbare Columns dagegen zuverlaessig weg (grid_forget).
    layout = [
      [main_view, pin_view, confirm_view, menu_view, editor_view, stand_view, camwait_view],
    ]

    window = sg.Window('Scheiben Video', layout, location=(0, 0), no_titlebar=True, keep_on_top=True, size=cfg.getProperty('screenSize'))

    #some speed optimisation - avoid searching every frame
    window_date = window['-DATE-']
    window_time = window['-TIME-']
    window_video = window['-VIDEO-']
    window_fps = window['-FPS-']

    logo_width = 110
    window.finalize()
    _show_page('-MAINVIEW-')
    # ressources/logo.png ist bewusst NICHT Teil des Repos (siehe README) -
    # jede Installation legt dort ihr eigenes Logo ab. Fehlt die Datei,
    # bleiben Sidebar-Logo und Blank-Screen-Wasserzeichen einfach leer statt
    # abzustuerzen.
    logo = cv2.imread('ressources/logo.png', cv2.IMREAD_UNCHANGED)
    blank_logo = None
    if logo is not None:
        blank_logo = logo.copy()  # unskalierte Variante fuer den Blank-Screen ("Video aus")
        logo = cv2.resize(logo, (logo_width,int((logo_width / logo.shape[1]) * logo.shape[0])))
        logobytes = cv2.imencode('.png', logo)[1].tobytes()
        window['-LOGO-'].update(data=logobytes)
    else:
        print("ressources/logo.png nicht gefunden - Logo-Anzeige bleibt leer.", file=sys.stderr)

    # --- Ersteinrichtungs-Assistent: Stand -> Ausschnitte -> PIN ---
    # Erzwungen (kein Abbrechen zum Hauptbildschirm) solange die jeweilige
    # Voraussetzung fehlt - jeder Schritt liest/schreibt ausschliesslich
    # Dateien auf /boot/firmware, nie nur In-Memory-Zustand: ein
    # Stromausfall/Neustart zu JEDEM Zeitpunkt fuehrt beim naechsten Start
    # einfach wieder zu genau dem Schritt, der noch fehlt.
    stands = load_stands()
    active_stand = load_active_stand(stands)
    while active_stand is None:
        stands = load_stands()
        chosen = run_stand_select(stands, forced=True)
        if chosen is not None and save_active_stand(chosen['id']):
            active_stand = chosen

    StreamPath = active_stand['url']
    _set_stand_name(active_stand['displayName'])

    # Kamera bewusst noch OHNE crop_region konstruiert - waehrend der
    # Ausschnitts-Kalibrierung unten wird ohnehin nur cap.getFrame(full=True)
    # gebraucht (siehe edit_section_points), das ist von crop_region
    # unabhaengig. crop_region wird weiter unten, sobald bekannt, direkt am
    # laufenden Camera-Objekt gesetzt (kein Neuaufbau/Reconnect noetig -
    # der Hintergrund-Thread liest das Attribut bei jedem Frame neu ein).
    cap = Camera(StreamPath)

    section_full_orig, section_detail_orig = load_sections(cfg)
    while section_full_orig is None or section_detail_orig is None:
        frame_or_back = _wait_for_camera_frame(cap)
        # isinstance-Check statt direktem "== 'back'": frame_or_back ist im
        # Erfolgsfall ein numpy-Array (der Kamera-Frame) - ein Array mit
        # einem String zu vergleichen wirft "ValueError: The truth value of
        # an array... is ambiguous" statt einfach False zu liefern (live am
        # Test-Pi als echter, durch SuccessExitStatus=1 verdeckter Absturz
        # aufgefallen).
        if isinstance(frame_or_back, str) and frame_or_back == 'back':
            # Zurueck zur Stand-Auswahl, z.B. weil die URL falsch war -
            # danach muss diese Kamera-Verbindung durch eine neue ersetzt
            # werden, sobald ein (ggf. anderer) Stand feststeht.
            active_stand = None
            while active_stand is None:
                stands = load_stands()
                chosen = run_stand_select(stands, forced=True)
                if chosen is not None and save_active_stand(chosen['id']):
                    active_stand = chosen
            StreamPath = active_stand['url']
            _set_stand_name(active_stand['displayName'])
            cap = Camera(StreamPath)
            continue
        which = 'full' if section_full_orig is None else 'detail'
        label = 'Ganze Scheibe' if which == 'full' else 'Innen Scheibe'
        # other_points: falls der jeweils ANDERE Bereich schon feststeht
        # (z.B. "Innen Scheibe" nach bereits gespeicherter "Ganze Scheibe"),
        # wird er als graue Referenz mitgezeichnet - gleiches Verhalten wie
        # im freiwilligen Settings-Menue.
        other = section_detail_orig if which == 'full' else section_full_orig
        result = edit_section_points(label, cap, None, other_points=other, allow_cancel=False)
        if result is not None:
            new_full = result if which == 'full' else section_full_orig
            new_detail = result if which == 'detail' else section_detail_orig
            save_sections_override(new_full, new_detail)
            section_full_orig, section_detail_orig = load_sections(cfg)

    SettingsPin = load_settings_pin()
    while SettingsPin == DEFAULT_PIN:
        new_pin = change_pin_flow(SettingsPin, forced=True)
        if new_pin is not None:
            SettingsPin = new_pin

    pts_full = np.array(section_full_orig, dtype="int")
    pts_detail = np.array(section_detail_orig, dtype="int")

    # nur den fuer section_full/section_detail benoetigten Bildausschnitt
    # aus der Kamera holen statt des vollen Frames - spart Kopier-/
    # Verarbeitungskosten, das Warp-Ergebnis bleibt dabei unveraendert
    CROP_MARGIN = 15
    crop_x0, crop_y0, crop_x1, crop_y1 = tl.crop_bounds([pts_full, pts_detail], CROP_MARGIN)
    pts_full = pts_full - [crop_x0, crop_y0]
    pts_detail = pts_detail - [crop_x0, crop_y0]
    cap.crop_region = (crop_x0, crop_y0, crop_x1, crop_y1)

    # Perspektiv-Matrizen einmalig berechnen statt bei jedem Frame neu -
    # pts_full/pts_detail aendern sich zur Laufzeit nie (nur ein Neustart
    # nach einer Settings-Aenderung setzt sie neu). dsize=VideoSize direkt
    # hier hineingerechnet spart ausserdem das bisher separate cv2.resize()
    # auf VideoSize nach dem Warp - cv2.warpPerspective() liefert das Bild
    # im Hot-Loop unten in einem Rutsch schon in der richtigen Groesse.
    M_full = tl.compute_perspective_matrix(pts_full, VideoSize)
    M_detail = tl.compute_perspective_matrix(pts_detail, VideoSize)

    _show_page('-MAINVIEW-')

    while True:
        if cap.is_stale(STREAM_STALE_TIMEOUT_SEC, STREAM_STARTUP_TIMEOUT_SEC):
            if cap.frame_id == 0:
                print(f"Kein Kamera-Frame innerhalb der Startup-Frist von {STREAM_STARTUP_TIMEOUT_SEC}s erhalten "
                      f"(seit Prozessstart: {time.time() - cap.start_time:.1f}s) - beende Prozess fuer Neustart.", file=sys.stderr)
            else:
                print(f"Kein neuer Kamera-Frame seit {time.time() - cap.last_frame_time:.1f}s "
                      f"(Schwelle {STREAM_STALE_TIMEOUT_SEC}s, zuletzt frame_id={cap.frame_id}) - beende Prozess fuer Neustart.", file=sys.stderr)
            # Exit-Code 2, NICHT 1: xinit gibt bei einem direkt an sich selbst
            # gerichteten SIGTERM (z.B. "systemctl stop/restart") selbst
            # Exit-Code 1 zurueck ("unexpected signal", siehe
            # targetdisplay.service.j2::SuccessExitStatus) - ein echter
            # Stream-Ausfall braucht einen eigenen, davon unterscheidbaren
            # Code, sonst wuerde SuccessExitStatus=1 auch echte Ausfaelle
            # faelschlich als Erfolg werten.
            sys.exit(2)

        event, values = window.read(timeout=10)
        ### Button handling
        if event in (sg.WIN_CLOSED, 'Exit'):
            break
        elif event == '-TOGGLEVIDEO-':
            displayVideo = not displayVideo
            if displayVideo:
              window['-TOGGLEVIDEO-'].update('Video aus')
              frame_count = 1
              startupTime = datetime.now()
              frame_timestamps.clear()
              zoom_disabled(False)
              blink_disabled(False)
              timer_disabled(False)
            else:
              window['-TOGGLEVIDEO-'].update('Video ein')
              frame = np.zeros((VideoSize[1], VideoSize[0], 3), np.uint8)
              if blank_logo is not None:
                frame = blend_logo_centered(frame, blank_logo)
              draw_image(window_video, frame)
              zoom_disabled(True)
              blink_disabled(True)
              timer_disabled(True)
        elif event == '-SETTINGS-':
            if check_pin(SettingsPin):
                if run_settings_flow(cap, section_full_orig, section_detail_orig, stands, SettingsPin):
                    # Neue Werte sind persistiert (Boot-Partition-Override) -
                    # sauberster Weg fuer einen neu berechneten Crop-Bereich
                    # ist ein Neustart des Prozesses; Restart=always im
                    # systemd-Unit startet main.py sofort mit den neuen
                    # Werten neu (Exit-Code 0 = regulaeres Beenden).
                    sys.exit(0)
        elif event == '-RESTART-':
            if check_pin(SettingsPin):
                if confirm_reboot():
                    subprocess.run(['/usr/bin/sudo', '/usr/sbin/reboot'])
        elif event == '-FULL_VIDEO-':
          zoom_level = 'full'
          zoom_center = []
        elif event == '-DETAIL_VIDEO-':
          zoom_level = 'detail'
          zoom_center = []
        elif event == '-VIDEO-':
          if zoom_center == []:
            # init zoom
            zoom_center = values["-VIDEO-"] 
          else:
            move_speed = 5
            # move zoomed window
            if values["-VIDEO-"][0] < 30:
              zoom_center = zoom_center[0]-move_speed,zoom_center[1]
            elif values["-VIDEO-"][0] > 70:
              zoom_center = zoom_center[0]+move_speed,zoom_center[1]
            if values["-VIDEO-"][1] < 30:
              zoom_center = zoom_center[0],zoom_center[1]-move_speed
            elif values["-VIDEO-"][1] > 70:
              zoom_center = zoom_center[0],zoom_center[1]+move_speed

        elif event == '-RESETZOOM-':
          zoom_center = []
        elif event == '-BLINK_START-':
          zoom_disabled(True)
          timer_disabled(True)
          video_filter_disabled(True)
          window['-BLINK_START-'].update(disabled=True)
          window['-BLINK_STOP-'].update(disabled=False)
          window['-BLINK_REF-'].update(disabled=False)
          blink_ref = []
          blink = True
        elif event == '-BLINK_REF-':
          blink_ref = []
          blink = True
        elif event == '-BLINK_STOP-':
          zoom_disabled(False)
          timer_disabled(False)
          blink_disabled(False)  
          video_filter_disabled(False)  
          blink_ref = []
          blink = False
        elif event == '-TIMER_5_3_7-':
          zoom_disabled(True)
          blink_disabled(True)
          video_filter_disabled(True)
          displayTimer = True
          window['-TIMER_5_3_7-'].update(disabled=True)
          window['-TIMER_20-'].update(disabled=True)
          window['-TIMER_10-'].update(disabled=True)
          window['-TIMER_STOP-'].update(disabled=False)
          displayVideo = False
          timerType = event
          timerStart = datetime.now()
          timerCurrentLoop = 0
        elif event == '-TIMER_20-':
          zoom_disabled(True)
          blink_disabled(True)
          video_filter_disabled(True)
          displayTimer = True
          window['-TIMER_5_3_7-'].update(disabled=True)
          window['-TIMER_20-'].update(disabled=True)
          window['-TIMER_10-'].update(disabled=True)
          window['-TIMER_STOP-'].update(disabled=False)
          displayVideo = False
          timerType = event
          timerStart = datetime.now()
        elif event == '-TIMER_10-':
          zoom_disabled(True)
          blink_disabled(True)
          video_filter_disabled(True)
          displayTimer = True
          window['-TIMER_5_3_7-'].update(disabled=True)
          window['-TIMER_20-'].update(disabled=True)
          window['-TIMER_10-'].update(disabled=True)
          window['-TIMER_STOP-'].update(disabled=False)
          displayVideo = False
          timerType = event
          timerStart = datetime.now()
        elif event == '-TIMER_STOP-':
          zoom_disabled(False)
          blink_disabled(False)
          video_filter_disabled(False)
          displayTimer = False
          displayVideo = True
          window['-TIMER_5_3_7-'].update(disabled=False)
          window['-TIMER_10-'].update(disabled=False)
          window['-TIMER_20-'].update(disabled=False)
          window['-TIMER_STOP-'].update(disabled=True)
          

        ### Image handling
        if displayVideo:
          # skip reprocessing/redrawing if the camera hasn't delivered a new frame yet
          current_frame_id = cap.frame_id
          frame = cap.getFrame() if current_frame_id != last_frame_id else None
          if frame is not None:
            last_frame_id = current_frame_id
            frame = cv2.warpPerspective(frame, M_full if not zoom_level == 'detail' else M_detail, VideoSize)

            #ready to display, all image manipulations are done only display options from here
            #--------------------------------------------------------------------------
            #blink
            if ((blink) & (len(blink_ref)==0)): blink_ref = frame
            if ((blink) & (datetime.now().second % 2)==1): frame = blink_ref

            # zoom
            if zoom_center != []: frame = tl.crop(frame,3, zoom_center)

            draw_image(window_video, frame)
            frame_count += 1
            now = datetime.now()
            frame_timestamps.append(now)
            while frame_timestamps and (now - frame_timestamps[0]).total_seconds() > FPS_WINDOW_SECONDS:
              frame_timestamps.popleft()
            try:
              window_span = (now - frame_timestamps[0]).total_seconds()
              fps = len(frame_timestamps) / window_span
              window_fps.update(f'FPS: {str(round(fps,1)) } - Frame { str(frame_count) }')
            except ZeroDivisionError:
              pass
        elif displayTimer:
          # Bugfix: (Breite, Hoehe) statt der von numpy erwarteten
          # (Hoehe, Breite) Reihenfolge - fiel bisher nicht auf, weil
          # video.size in der Praxis immer quadratisch war (600x600/780x780).
          # Der Blank-Screen-Handler oben (-TOGGLEVIDEO-) macht es bereits
          # richtig herum, hier war es offenbar abgeschrieben und dabei
          # vertauscht worden.
          frame = np.zeros((VideoSize[1],VideoSize[0],3), np.uint8)
          tmpTimerSecs = (datetime.now()-timerStart).seconds

          prepTime = 7
          stopTime = 3

          if timerType == "-TIMER_5_3_7-":
            showTime = 3
            hideTime = 7
            loopCounter = 5
            baseTime = (timerCurrentLoop * (showTime + hideTime)) + prepTime
            if (tmpTimerSecs < prepTime):
              #red
              frame[:] = (0, 0, 255)
              cv2.putText(frame, str(prepTime - tmpTimerSecs), (20,VideoSize[1]-20), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            elif ((tmpTimerSecs - baseTime) < showTime):
              #green
              frame[:] = (0, 255, 0)
              cv2.putText(frame, str(timerCurrentLoop + 1), (20,130), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
              cv2.putText(frame, str(baseTime + showTime - tmpTimerSecs), (20,VideoSize[1]-20), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            elif((tmpTimerSecs - baseTime - showTime) < hideTime):
              #red 
              frame[:] = (0, 0, 255)
              cv2.putText(frame, str(timerCurrentLoop +1), (20,130), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
              cv2.putText(frame, str(baseTime + showTime + hideTime - tmpTimerSecs), (20,VideoSize[1]-20), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            else:
              #red
              if (timerCurrentLoop < loopCounter -1): 
                timerCurrentLoop += 1
              else:
                window['-TIMER_STOP-'].Click()
              frame[:] = (0, 0, 255)
          if timerType == "-TIMER_20-":
            showTime = 20
            if(tmpTimerSecs < prepTime):
              #red
              frame[:] = (0, 0, 255)
              cv2.putText(frame, str(prepTime - tmpTimerSecs), (20,VideoSize[1]-20), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            elif tmpTimerSecs < (prepTime + showTime):
              #green
              frame[:] = (0, 255, 0)
              cv2.putText(frame, str(prepTime + showTime - tmpTimerSecs), (20,VideoSize[1]-20), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            elif tmpTimerSecs < (prepTime + showTime + stopTime):
              #red
              frame[:] = (0, 0, 255)
            else:
              window['-TIMER_STOP-'].Click()
          if timerType == "-TIMER_10-":
            showTime = 10
            if(tmpTimerSecs < prepTime):
              #red
              frame[:] = (0, 0, 255)
              cv2.putText(frame, str(prepTime - tmpTimerSecs), (20,VideoSize[1]-20), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            elif tmpTimerSecs < (prepTime + showTime):
              #green
              frame[:] = (0, 255, 0)
              cv2.putText(frame, str(prepTime + showTime - tmpTimerSecs), (20,VideoSize[1]-20), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
            elif tmpTimerSecs < (prepTime + showTime + stopTime):
              #red
              frame[:] = (0, 0, 255)
            else:
              window['-TIMER_STOP-'].Click()
          draw_image(window_video, frame)
          window_fps.update('')

        now = datetime.now()
        window_date.update(now.strftime("%d.%m.%Y"))
        window_time.update(now.strftime("%H:%M:%S"))
    window.close()


main()
