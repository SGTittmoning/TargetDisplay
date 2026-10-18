"""Einstellungen auf der Boot-Partition: Ausschnitte, Stand-Liste und
-Auswahl, PIN.

Alle Lesefunktionen fangen jeden Fehler ab (except Exception) und liefern
dann "nicht eingerichtet" bzw. den Standard: eine kaputte oder unerwartete
Datei darf die App nie zum Absturz bringen, der Assistent fragt den Schritt
stattdessen neu ab.
"""
import json
import os
import subprocess
import sys

import transformlib

# Ausschnitte, die ueber die Einstellungen geaendert werden. Sie liegen
# auf der Boot-Partition statt in config.yml: das Root-Dateisystem laeuft
# mit Overlay, eine Aenderung dort waere nach einem Neustart weg. Existiert
# die Datei, gilt sie vor config.yml.
SECTIONS_OVERRIDE_FILE = '/boot/firmware/targetdisplay-sections.json'


# Stand-Liste (Anzeigename und Kamera-URL je Stand), per Ansible auf alle
# Geraete verteilt (ansible/tasks/stands.yml). Welchen Stand ein Geraet
# zeigt, steht in ACTIVE_STAND_FILE; gewaehlt im Assistenten.
STANDS_FILE = '/boot/firmware/targetdisplay-stands.json'


ACTIVE_STAND_FILE = '/boot/firmware/targetdisplay-active-stand.json'


# Standard-PIN, wenn weder config.yml noch PIN_FILE einen setzen. Der
# Assistent erzwingt dann eine Aenderung (siehe change_pin_flow()), eine
# Installation braucht deshalb keinen PIN vorab.
DEFAULT_PIN = '1234'

# Erlaubte PIN-Laenge (Ziffern), fuer Pruefung und Ziffernfeld.
PIN_MIN_LEN, PIN_MAX_LEN = 4, 6


PIN_FILE = '/boot/firmware/targetdisplay-pin.json'


def load_sections(cfg):
    # Fehlen die Ausschnitte in config.yml und der Datei, ist das kein Fehler:
    # (None, None) startet den Assistenten.
    section_full = cfg.get_property_with_default('video.section_full', None)
    section_detail = cfg.get_property_with_default('video.section_detail', None)
    if os.path.exists(SECTIONS_OVERRIDE_FILE):
        try:
            with open(SECTIONS_OVERRIDE_FILE, encoding='utf-8') as f:
                override = json.load(f)
            section_full = override.get('section_full', section_full)
            section_detail = override.get('section_detail', section_detail)
            print(f"Sections-Override aus {SECTIONS_OVERRIDE_FILE} geladen.", file=sys.stderr)
        except Exception as e:
            # Eine kaputte Datei (z.B. Stromausfall beim Schreiben) gilt als "noch
            # nicht eingerichtet"; der Assistent fragt den Schritt erneut ab.
            print(f"Konnte Sections-Override nicht lesen ({e}), nutze config.yml-Werte.", file=sys.stderr)
    return _valid_section(section_full, 'section_full'), _valid_section(section_detail, 'section_detail')


def _valid_section(points, name):
    # Ein Ausschnitt muss ein brauchbares Viereck aus vier Zahlenpaaren sein,
    # sonst scheitert die Entzerrung beim Start. Alles andere gilt als
    # "nicht eingerichtet", der Assistent fragt den Ausschnitt neu ab.
    if points is None:
        return None
    try:
        if transformlib.is_valid_quad(points):
            return points
    except (TypeError, ValueError):
        pass
    print(f"Ausschnitt {name} ungueltig ({points!r:.80}), gilt als nicht eingerichtet.", file=sys.stderr)
    return None


def _run_save_script(script_name, payload):
    # Ruft eines der Speicher-Skripte per sudo auf (eines je Aktion, siehe
    # ansible/tasks/settings_sudo.yml) und uebergibt das JSON ueber stdin.
    try:
        result = subprocess.run(
            ['/usr/bin/sudo', f'/root/bin/{script_name}'],
            input=payload, text=True, capture_output=True, timeout=15, check=False
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
    # Einzelne Ausschnitte duerfen None sein: der Assistent speichert erst
    # "Ganze Scheibe", dann "Innen Scheibe". None wird als null geschrieben und
    # von load_sections() als "noch nicht eingerichtet" gelesen.
    payload = json.dumps({
        'section_full': _points_to_json(section_full),
        'section_detail': _points_to_json(section_detail),
    })
    return _run_save_script('targetdisplay-save-sections.sh', payload)


def load_stands():
    # Fehlt die Datei oder ist sie kaputt: leere Liste. Die Stand-Auswahl
    # zeigt dann einen Hinweis.
    if not os.path.exists(STANDS_FILE):
        return []
    try:
        with open(STANDS_FILE, encoding='utf-8') as f:
            data = json.load(f)
        return [s for s in data.get('stands', []) if s.get('id') and s.get('displayName') and s.get('url')]
    except Exception as e:
        print(f"Konnte {STANDS_FILE} nicht lesen ({e}), keine Stand-Liste verfuegbar.", file=sys.stderr)
        return []


def load_active_stand(stands, cfg):
    # Ohne Auswahl-Datei gilt der Stand aus config.yml (Einzel-Stand-Betrieb,
    # siehe config.yml.dist). Gibt es die Datei, hat sie Vorrang.
    cfg_url = cfg.get_property_with_default('video.url', None)
    cfg_name = cfg.get_property_with_default('standName', None)
    fallback = {'id': None, 'displayName': cfg_name, 'url': cfg_url} if cfg_url else None

    if not os.path.exists(ACTIVE_STAND_FILE):
        return fallback
    try:
        with open(ACTIVE_STAND_FILE, encoding='utf-8') as f:
            active = json.load(f)
        stand_id = active.get('id')
        for s in stands:
            if s['id'] == stand_id:
                return s
        # Die ID steht (noch) nicht in der Stand-Liste: wie "nicht ausgewaehlt"
        # behandeln, der Assistent (run_setup_wizard) zeigt die Auswahl erneut.
        print(f"Aktive Stand-ID '{stand_id}' nicht in {STANDS_FILE} gefunden, behandle als nicht ausgewaehlt.", file=sys.stderr)
        return fallback
    except Exception as e:
        print(f"Konnte {ACTIVE_STAND_FILE} nicht lesen ({e}), behandle als nicht ausgewaehlt.", file=sys.stderr)
        return fallback


def save_active_stand(stand_id):
    return _run_save_script('targetdisplay-save-active-stand.sh', json.dumps({'id': stand_id}))


def is_valid_pin(pin):
    # Nur was sich ueber das Ziffernfeld eingeben laesst: 4-6 Ziffern 0-9.
    return pin.isascii() and pin.isdigit() and PIN_MIN_LEN <= len(pin) <= PIN_MAX_LEN


def load_settings_pin(cfg):
    # Ein PIN, der sich nicht eingeben laesst (z.B. der Platzhalter
    # "CHANGEME" aus config.yml.dist), gilt als DEFAULT_PIN. Der Assistent
    # erzwingt dann eine Aenderung, statt die Standaufsicht auszusperren.
    pin = cfg.get_property_with_default('settingsPin', DEFAULT_PIN)
    if os.path.exists(PIN_FILE):
        try:
            with open(PIN_FILE, encoding='utf-8') as f:
                override = json.load(f)
            pin = override.get('pin', pin)
        except Exception as e:
            print(f"Konnte {PIN_FILE} nicht lesen ({e}), nutze bisherigen PIN.", file=sys.stderr)
    pin = str(pin)
    if not is_valid_pin(pin):
        print('Konfigurierter PIN ist keine 4-6-stellige Zahl, Standard-PIN gilt.', file=sys.stderr)
        return DEFAULT_PIN
    return pin


def save_pin(new_pin):
    return _run_save_script('targetdisplay-save-pin.sh', json.dumps({'pin': new_pin}))
