import json
import os
import subprocess
import sys

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


def load_active_stand(stands, cfg):
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


def load_settings_pin(cfg):
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
