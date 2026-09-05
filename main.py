import sys
import time
import signal
import json
import os
import math
import queue
import subprocess
import cv2
import tkinter as tk
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

version = '0.11.0'

cfg = config.load("config.yml")

window = None

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
# Fenster wird nur EINMAL beim Programmstart gebaut (siehe main()/_show_page
# unten - PIN/Settings/Restart sind eigene "Seiten" im selben Fenster, per
# Frame.tkraise() umgeschaltet), das tatsaechliche Kamerabild-
# Seitenverhaeltnis ist zu diesem Zeitpunkt aber noch nicht bekannt. Das
# Bild wird beim Zeichnen einfach oben links in dieser Flaeche platziert
# (siehe to_display_frame/redraw).
EDITOR_MAX_W, EDITOR_MAX_H = 1000, 620

# Zusaetzliche, grobe Zeichenbegrenzung fuer den Standnamen - der eigentliche
# Schutz gegen ein auseinandergedruecktes Layout ist der feste Pixel-Rahmen
# um das Label in Window._build_main_view() (Frame mit fester width/height +
# pack_propagate(False)). Diese Kuerzung hier ist nur eine zusaetzliche
# Sicherheitsmarge, damit gar nicht erst extrem lange Strings an Tk
# uebergeben werden.
STANDNAME_MAX_CHARS = 30

# Helles Farbschema (Redesign 2026-09, ueber vier Design-Canvas-Runden mit
# dem Nutzer abgestimmt - loest das alte PySimpleGUI-"LightGreen"-Theme ab).
# Je Funktionsgruppe auf dem Hauptbildschirm (Zoom/Blinken/Timer) eine eigene
# Akzentfarbe statt Rahmen zur Unterscheidung; jede Akzentfarbe hat
# zusaetzlich eine abgeschwaechte "Muted"-Variante fuer deaktivierte Buttons
# (statt nur ausgegrautem Text) - siehe _make_accent_button()/
# _set_icon_buttons() unten.
BG = '#f4f6f5'
FG_DARK = '#1c2024'
FG_MUTED = '#9aa7b3'

ACCENT_ZOOM = '#3b6ea5'
ACCENT_ZOOM_MUTED_BG = '#dfe7ee'
ACCENT_ZOOM_MUTED_FG = '#9aa7b3'

ACCENT_BLINK = '#c17f27'
ACCENT_BLINK_MUTED_BG = '#f1e3cf'
ACCENT_BLINK_MUTED_FG = '#c2a677'

ACCENT_TIMER = '#a5433b'
ACCENT_TIMER_MUTED_BG = '#f4dcda'
ACCENT_TIMER_MUTED_FG = '#c98f89'

NEUTRAL_BG = '#eef1f0'
NEUTRAL_BORDER = '#dde3e1'
NEUTRAL_FG = '#5a6570'

DATETIME_BG = '#e3e8e6'

# Vorgerenderte Icon-PNGs (dev-time per Pillow erzeugt, siehe
# ressources/icons/README fehlt bewusst - main.py braucht KEIN Pillow zur
# Laufzeit, genau wie ressources/logo.png schon immer ein statisches Asset
# war). __file__-relativ statt "ressources/..." direkt, damit main.py
# unabhaengig vom aktuellen Arbeitsverzeichnis funktioniert.
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ressources', 'icons')

WIN_CLOSED = '__WIN_CLOSED__'
TIMEOUT_EVENT = '__TIMEOUT__'


class Elem:
    # Duenner Wrapper um ein natives Tk-Widget, der nur die .update(...)-
    # Aufrufmuster abdeckt, die in diesem Skript tatsaechlich vorkommen -
    # kein Nachbau von PySimpleGUI, nur genug, um window['-KEY-'].update(...)
    # unveraendert weiterzuverwenden und dadurch den Rest der Datei (State-
    # Machine, Event-Handling) fast unveraendert vom PySimpleGUI-Original
    # uebernehmen zu koennen.
    def __init__(self, widget, show=None):
        self.widget = widget
        # show: die exakten pack()-Kwargs, mit denen das Widget urspruenglich
        # sichtbar gemacht wurde - fuer die drei Widgets mit visible=-Toggle
        # (-PIN_CANCEL-, -EDIT_CANCEL-, -STAND_BACK-) explizit beim
        # Registrieren mitgegeben, statt sie erst beim ersten Verstecken per
        # w.pack_info() aus Tk zurueckzufragen. Live auf dem Test-Pi
        # (Debian Trixie, Python 3.13) ist genau diese pack_info()-Abfrage
        # beim ALLERERSTEN Verstecken mit "_tkinter.TclError: window ...
        # isn't packed" gescheitert, obwohl das Widget nachweislich schon
        # bei der Konstruktion gepackt wurde (auf Python 3.12 lokal nicht
        # reproduzierbar - vermutlich eine Tcl/Tk-Versions-Eigenheit). Die
        # pack()-Optionen sind zur Erstellungszeit ohnehin exakt bekannt,
        # eine spaetere Tk-Rueckfrage ist unnoetig und genau die fragile
        # Stelle.
        self._show_kwargs = show
        self._image_ref = None

    def update(self, value=None, disabled=None, visible=None, values=None, text_color=None, data=None):
        w = self.widget
        if values is not None:
            w.delete(0, tk.END)
            for v in values:
                w.insert(tk.END, v)
        if value is not None:
            w.config(text=value)
        if text_color is not None:
            w.config(fg=text_color)
        if disabled is not None:
            w.config(state=(tk.DISABLED if disabled else tk.NORMAL))
        if data is not None:
            img = tk.PhotoImage(data=data)
            self._image_ref = img  # Referenz halten, sonst wird das Tk-Image sofort freigegeben
            w.config(image=img)
        if visible is not None:
            if visible:
                w.pack(**(self._show_kwargs or {}))
            else:
                w.pack_forget()


class Window:
    # Ersetzt sg.Window: EIN Tk-Root mit mehreren als Geschwister-Frames
    # angelegten "Seiten" (siehe _PAGE_KEYS), zwischen denen per
    # Frame.tkraise() umgeschaltet wird (siehe show_page). read()/post()
    # bilden das blockierende window.read()-Verhalten von PySimpleGUI nach:
    # jedes Button-Kommando legt sein Event in eine Queue, read() pumpt den
    # Tk-Eventloop per periodischem root.update() und liefert das naechste
    # Event (oder nach Ablauf von timeout ein TIMEOUT_EVENT). Genau dieses
    # Verhalten hat PySimpleGUI intern ohnehin schon so umgesetzt - hier nur
    # ohne die Abstraktionsschicht dazwischen.
    def __init__(self, cfg, video_size):
        self.video_size = video_size
        self.screen_size = cfg.getProperty('screenSize')
        self._queue = queue.Queue()
        self.widgets = {}
        self.pages = {}
        self._video_image = None
        self._video_image_id = None

        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes('-topmost', True)
        screen = self.screen_size
        self.root.geometry(f'{screen[0]}x{screen[1]}+0+0')
        self.root.configure(bg=BG)
        # Globale Button-Optik ueber die Tk-Optionsdatenbank: gilt fuer alle
        # "einfachen" Dialog-Buttons (PIN-Tastenfeld, Settings-Menue,
        # Bestaetigen, Editor, Stand-Auswahl, Kamera-Warteseite) als
        # neutrale Grundoptik, flach statt des alten 3D-Reliefs. Die
        # farbcodierten Hauptbildschirm-Buttons (Zoom/Blinken/Timer) und die
        # beiden Header-Icon-Buttons setzen ihre Farben/Icons explizit selbst
        # (siehe _make_accent_button()) und ueberschreiben diese Vorgabe pro
        # Widget - einzelne .config()-Aufrufe haben in Tk immer Vorrang vor
        # der Optionsdatenbank.
        self.root.option_add('*Button.background', NEUTRAL_BG)
        self.root.option_add('*Button.foreground', FG_DARK)
        self.root.option_add('*Button.disabledForeground', FG_MUTED)
        self.root.option_add('*Button.activeBackground', NEUTRAL_BG)
        self.root.option_add('*Button.activeForeground', FG_DARK)
        self.root.option_add('*Button.relief', 'flat')
        self.root.option_add('*Button.borderWidth', 0)
        self.root.protocol('WM_DELETE_WINDOW', lambda: self.post(WIN_CLOSED))

        self.icons = {}
        self._btn_style = {}

        self.container = tk.Frame(self.root, bg=BG)
        self.container.pack(fill='both', expand=True)
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self._build_main_view()
        self._build_pin_view()
        self._build_confirm_view()
        self._build_menu_view()
        self._build_editor_view()
        self._build_stand_view()
        self._build_camwait_view()

    # -- Hilfsfunktionen Fensteraufbau -----------------------------------

    def _new_page(self, key):
        f = tk.Frame(self.container, bg=BG)
        f.grid(row=0, column=0, sticky='nsew')
        self.pages[key] = f
        return f

    def _reg(self, key, widget, show=None):
        self.widgets[key] = Elem(widget, show=show)
        return widget

    def _icon(self, name):
        # Cache haelt die tk.PhotoImage-Referenzen dauerhaft am Leben (Tk
        # gibt ein Image sofort frei, sobald keine Python-Referenz mehr
        # existiert) - dieselbe Notwendigkeit wie Elem._image_ref.
        img = self.icons.get(name)
        if img is None:
            img = tk.PhotoImage(file=os.path.join(ICON_DIR, name + '.png'))
            self.icons[name] = img
        return img

    def _make_accent_button(self, parent, key, text, accent_bg, accent_fg,
                             muted_bg, muted_fg, icon_on=None, icon_off=None,
                             start_enabled=True, font=('Helvetica', 15, 'bold')):
        # Gemeinsamer Baustein fuer alle farbcodierten Hauptbildschirm-Buttons
        # (Zoom/Blinken/Timer, inkl. des reinen Text-Buttons "Timer Stop").
        # Tk faerbt einen deaktivierten Button NICHT automatisch um (nur der
        # Text wird ueber disabledforeground blass) - der eigentliche
        # "deaktiviert"-Look (helle Muted-Flaeche statt satter Akzentfarbe,
        # Design-Runde 2) wird stattdessen explizit hier UND in
        # _set_icon_buttons() (bei jedem Enable/Disable danach) gesetzt.
        # disabledforeground wird einmalig fest auf muted_fg gesetzt - dieser
        # Wert kommt ohnehin nur zur Geltung, waehrend der Button disabled
        # ist, muss also bei einem State-Wechsel nicht erneut angefasst
        # werden (nur bg/fg/image aendern sich dynamisch).
        icon = (icon_on if start_enabled else icon_off) if icon_on is not None else None
        bg = accent_bg if start_enabled else muted_bg
        fg = accent_fg if start_enabled else muted_fg
        kwargs = dict(text=text, bg=bg, fg=fg, disabledforeground=muted_fg,
                      activebackground=bg, activeforeground=fg,
                      bd=0, relief='flat', highlightthickness=0, font=font,
                      wraplength=140, justify='center',
                      state=(tk.NORMAL if start_enabled else tk.DISABLED),
                      command=lambda: self.post(key))
        if icon is not None:
            kwargs.update(image=icon, compound='top')
        b = tk.Button(parent, **kwargs)
        self._btn_style[key] = dict(icon_on=icon_on, icon_off=icon_off,
                                     bg_on=accent_bg, fg_on=accent_fg,
                                     bg_off=muted_bg, fg_off=muted_fg)
        self._reg(key, b)
        return b

    def __getitem__(self, key):
        return self.widgets[key]

    def post(self, key, values=None):
        self._queue.put((key, values or {}))

    def read(self, timeout=None):
        deadline = None if timeout is None else time.monotonic() + timeout / 1000.0
        while True:
            try:
                return self._queue.get_nowait()
            except queue.Empty:
                pass
            self.root.update()
            if deadline is not None and time.monotonic() >= deadline:
                return (TIMEOUT_EVENT, {})
            time.sleep(0.005)

    def refresh(self):
        self.root.update()

    def show_page(self, key):
        self.pages[key].tkraise()
        self.root.update()

    def close(self):
        self.root.destroy()

    def popup(self, message):
        top = tk.Toplevel(self.root)
        top.overrideredirect(True)
        top.attributes('-topmost', True)
        top.configure(bg='white', highlightthickness=2, highlightbackground='black')
        tk.Label(top, text=message, font=('Helvetica', 16), bg='white',
                 wraplength=420, justify='center').pack(padx=30, pady=(30, 15))
        tk.Button(top, text='OK', font=('Helvetica', 14), width=10, height=2,
                  command=top.destroy).pack(pady=(0, 25))
        top.update_idletasks()
        w, h = top.winfo_width(), top.winfo_height()
        sw, sh = self.root.winfo_width(), self.root.winfo_height()
        top.geometry(f'+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}')
        top.grab_set()
        top.wait_window()

    def draw_image(self, frame):
        # PPM statt PNG: tk.PhotoImage(data=...) akzeptiert PPM-Bytes direkt,
        # ohne Kompression - dieselbe Begruendung/Messung wie zuvor unter
        # PySimpleGUI (das intern fuer Graph.draw_image() exakt denselben
        # tk.PhotoImage(data=...)-Aufruf gemacht hat, siehe INTERNAL_NOTES
        # Performance-Abschnitt). itemconfig() statt delete+neu erzeugen
        # spart zusaetzlich die vorherige delete_figure-Buchhaltung.
        imgbytes = cv2.imencode('.ppm', frame)[1].tobytes()
        photo = tk.PhotoImage(data=imgbytes)
        self._video_image = photo
        if self._video_image_id is None:
            self._video_image_id = self.video_canvas.create_image(0, 0, anchor='nw', image=photo)
        else:
            self.video_canvas.itemconfig(self._video_image_id, image=photo)

    # -- Seiten ------------------------------------------------------------

    def _build_main_view(self):
        page = self._new_page('-MAINVIEW-')

        # Einheitlicher Aussenabstand auf allen 4 Seiten, aus config.yml
        # berechnet statt hartkodiert: die Bildhoehe (video.size.y) ist
        # praktisch immer der engste Faktor (bei 800px Bildschirmhoehe und
        # 780px Bildhoehe bleiben nur 20px insgesamt, also 10px oben+unten -
        # live am Test-Pi gemessen, urspruenglich 0px links vs. 60px rechts
        # uebrig, deutlich asymmetrisch). Der ueberschuessige horizontale
        # Platz landet nicht als reiner rechter Rand, sondern sichtbar als
        # Luecke zwischen Button-Spalte und Video (siehe spacer unten) -
        # dadurch bleibt der Aussenrand auf allen 4 Seiten exakt gleich.
        margin = max(0, (self.screen_size[1] - self.video_size[1]) // 2)

        # Feste 450px-Breite (statt wie zuvor inhaltsbestimmt) - exakt der
        # Wert aus den mit dem Nutzer abgestimmten Design-Canvas-Mockups
        # (Design-Runden 1-4). pack_propagate(False) erzwingt das hart, die
        # drei Button-Reihen darunter teilen sich diese Breite ueber
        # fill='both'+expand=True gleichmaessig auf (siehe pack_equal()) -
        # das Tk-Aequivalent zu "grid-template-columns: repeat(3, 1fr)" im
        # Mockup, da Tk kein CSS-Grid kennt.
        left = tk.Frame(page, bg=BG, width=450)
        left.pack(side='left', fill='y', padx=(margin, 0), pady=margin)
        left.pack_propagate(False)

        spacer = tk.Frame(page, bg=BG)
        spacer.pack(side='left', fill='both', expand=True)

        video_canvas = tk.Canvas(page, width=self.video_size[0], height=self.video_size[1],
                                  bg='black', highlightthickness=0)
        video_canvas.pack(side='left', padx=(0, margin))
        video_canvas.bind('<Button-1>', self._on_video_click)
        self.video_canvas = video_canvas

        # -- Header: Standname links, zwei kleine neutrale Icon-Buttons
        # rechts (Video aus, Settings mit Schloss-Badge) - bewusst NICHT
        # mehr so prominent wie die vorherigen breiten Textbuttons
        # (Nutzer-Feedback Design-Runde 3: "Settings-Zugang muss nicht so
        # prominent sein"). Restart ist ausschliesslich ueber Settings ->
        # Menue erreichbar (siehe _build_menu_view/run_settings_flow),
        # kein eigener Button mehr auf der Hauptseite.
        header = tk.Frame(left, bg=BG)
        header.pack(side='top', fill='x', pady=(0, 18))

        # sg.Text(size=...) allein reicht NICHT: das ist bei Tk nur eine
        # Mindestbreite in Zeichen, kein Maximum - bei fetter/grosser Schrift
        # wird das Element trotzdem breiter als size= wenn der Inhalt es
        # verlangt. Der Standname kommt seit der Mehr-Stand-Faehigkeit aus
        # der frei editierbaren targetdisplay-stands.json, nicht mehr aus
        # einer kurzen, kontrollierten Ansible-Variable - ein zu langer Name
        # hat live am Test-Pi das ganze Layout auseinandergedrueckt. Ein
        # Frame mit fester width/height + pack_propagate(False) erzwingt
        # dagegen eine wirklich harte Breite - ueberstehender Inhalt wird
        # abgeschnitten statt den Frame zu vergroessern.
        name_frame = tk.Frame(header, width=280, height=38, bg=BG)
        name_frame.pack(side='left')
        name_frame.pack_propagate(False)
        name_label = tk.Label(name_frame, text='', font=('Helvetica', 24, 'bold'),
                               bg=BG, fg=FG_DARK, anchor='w')
        name_label.pack(fill='both', expand=True)
        self._reg('-STANDNAME-', name_label)

        icon_row = tk.Frame(header, bg=BG)
        icon_row.pack(side='right')

        def make_header_icon_button(key, icon_name, command):
            b = tk.Button(icon_row, image=self._icon(icon_name), width=46, height=46,
                          bg=NEUTRAL_BG, activebackground=NEUTRAL_BG, bd=0, relief='flat',
                          highlightthickness=1, highlightbackground=NEUTRAL_BORDER,
                          command=command)
            self._reg(key, b)
            return b

        video_btn = make_header_icon_button('-TOGGLEVIDEO-', 'eye_slash_neutral',
                                             lambda: self.post('-TOGGLEVIDEO-'))
        video_btn.pack(side='left', padx=(0, 10))
        settings_btn = make_header_icon_button('-SETTINGS-', 'settings_lock_neutral',
                                                lambda: self.post('-SETTINGS-'))
        settings_btn.pack(side='left')

        # BTN_GAP: sichtbarer Abstand zwischen benachbarten Touch-Buttons
        # (Finger sind ungenauer als ein Mauszeiger - direkt aneinander-
        # stossende Buttons riskieren Fehltreffer auf den Nachbar-Button).
        # GROUP_GAP: Abstand zwischen den drei Funktionsgruppen. Die
        # frueheren tk.LabelFrame-Rahmen ("Zoom"/"Blinken"/"Timer") sind
        # durch eine schlichte Grossbuchstaben-Caption in der jeweiligen
        # Akzentfarbe ersetzt (Design-Runde 2/3) - kein Rahmen mehr noetig,
        # die Farbe der Buttons selbst uebernimmt die Gruppierung.
        BTN_GAP = 10
        GROUP_GAP = 20
        BTN_HEIGHT = 78

        groups = tk.Frame(left, bg=BG)
        groups.pack(side='top', fill='x')

        def make_caption(parent, text, color):
            tk.Label(parent, text=text.upper(), font=('Helvetica', 13, 'bold'),
                     bg=BG, fg=color, anchor='w').pack(side='top', anchor='w', pady=(0, 7))

        def pack_equal(buttons, gap=BTN_GAP):
            n = len(buttons)
            for i, b in enumerate(buttons):
                b.pack(side='left', fill='both', expand=True, padx=(0, gap) if i < n - 1 else 0)

        icon = self._icon

        # Zoom
        zoom_group = tk.Frame(groups, bg=BG)
        zoom_group.pack(side='top', fill='x', pady=(0, GROUP_GAP))
        make_caption(zoom_group, 'Zoom', ACCENT_ZOOM)
        zoom_row = tk.Frame(zoom_group, bg=BG)
        zoom_row.pack(side='top', fill='x')
        b1 = self._make_accent_button(zoom_row, '-FULL_VIDEO-', 'Ganze Scheibe',
                                       ACCENT_ZOOM, 'white', ACCENT_ZOOM_MUTED_BG, ACCENT_ZOOM_MUTED_FG,
                                       icon_on=icon('grid_white'), icon_off=icon('grid_zoom_muted'))
        b2 = self._make_accent_button(zoom_row, '-DETAIL_VIDEO-', 'Innen Scheibe',
                                       ACCENT_ZOOM, 'white', ACCENT_ZOOM_MUTED_BG, ACCENT_ZOOM_MUTED_FG,
                                       icon_on=icon('zoomin_white'), icon_off=icon('zoomin_zoom_muted'))
        # Reset ist eine dauerhaft deaktivierte Funktion (siehe zoom_disabled()
        # unten - der Aufrufer setzt es IMMER auf disabled=True, unabhaengig
        # vom Parameter) - braucht deshalb keine "weiss"-Variante, es zeigt
        # nie etwas anderes als seinen Muted-Zustand.
        b3 = self._make_accent_button(zoom_row, '-RESETZOOM-', 'Reset',
                                       ACCENT_ZOOM, 'white', ACCENT_ZOOM_MUTED_BG, ACCENT_ZOOM_MUTED_FG,
                                       icon_on=icon('undo_zoom_muted'), icon_off=icon('undo_zoom_muted'),
                                       start_enabled=False)
        for b in (b1, b2, b3):
            b.config(height=BTN_HEIGHT)
        pack_equal([b1, b2, b3])

        # Blinken
        blink_group = tk.Frame(groups, bg=BG)
        blink_group.pack(side='top', fill='x', pady=(0, GROUP_GAP))
        make_caption(blink_group, 'Blinken', ACCENT_BLINK)
        blink_row = tk.Frame(blink_group, bg=BG)
        blink_row.pack(side='top', fill='x')
        b1 = self._make_accent_button(blink_row, '-BLINK_START-', 'Start',
                                       ACCENT_BLINK, 'white', ACCENT_BLINK_MUTED_BG, ACCENT_BLINK_MUTED_FG,
                                       icon_on=icon('eye_white'), icon_off=icon('eye_blink_muted'))
        b2 = self._make_accent_button(blink_row, '-BLINK_REF-', 'Referenz',
                                       ACCENT_BLINK, 'white', ACCENT_BLINK_MUTED_BG, ACCENT_BLINK_MUTED_FG,
                                       icon_on=icon('target_white'), icon_off=icon('target_blink_muted'),
                                       start_enabled=False)
        b3 = self._make_accent_button(blink_row, '-BLINK_STOP-', 'Stop',
                                       ACCENT_BLINK, 'white', ACCENT_BLINK_MUTED_BG, ACCENT_BLINK_MUTED_FG,
                                       icon_on=icon('stopsquare_white'), icon_off=icon('stopsquare_blink_muted'),
                                       start_enabled=False)
        for b in (b1, b2, b3):
            b.config(height=BTN_HEIGHT)
        pack_equal([b1, b2, b3])

        # Timer
        timer_group = tk.Frame(groups, bg=BG)
        timer_group.pack(side='top', fill='x')
        make_caption(timer_group, 'Timer', ACCENT_TIMER)
        row1 = tk.Frame(timer_group, bg=BG)
        row1.pack(side='top', fill='x')
        b1 = self._make_accent_button(row1, '-TIMER_5_3_7-', '5 x 3/7 Sek.',
                                       ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                       icon_on=icon('clock_white'), icon_off=icon('clock_timer_muted'))
        b2 = self._make_accent_button(row1, '-TIMER_20-', '20 Sek.',
                                       ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                       icon_on=icon('clock_white'), icon_off=icon('clock_timer_muted'))
        b3 = self._make_accent_button(row1, '-TIMER_10-', '10 Sek.',
                                       ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                       icon_on=icon('clock_white'), icon_off=icon('clock_timer_muted'))
        for b in (b1, b2, b3):
            b.config(height=BTN_HEIGHT)
        pack_equal([b1, b2, b3])
        # Ohne image= interpretiert Tk width/height eines Buttons als
        # Text-ZEILEN/-ZEICHEN, nicht als Pixel (anders als bei den Icon-
        # Buttons oben) - "Timer Stop" hat bewusst kein Icon (reiner Text-
        # Button, siehe Design-Runde 2/3). Fuer eine exakte Pixelhoehe daher
        # derselbe Kniff wie bei name_frame oben: feste Frame-Hoehe +
        # pack_propagate(False), der Button selbst fuellt sie per fill='both'.
        row2 = tk.Frame(timer_group, bg=BG, height=46)
        row2.pack(side='top', fill='x', pady=(BTN_GAP, 0))
        row2.pack_propagate(False)
        stop_btn = self._make_accent_button(row2, '-TIMER_STOP-', 'Timer Stop',
                                             ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                             start_enabled=False, font=('Helvetica', 14, 'bold'))
        stop_btn.pack(side='left', fill='both', expand=True)

        # -- Footer (Design-Runde 4, Variante B: Logo mittig oben, Uhrzeit
        # darunter zentriert ueber die volle Breite - vom Nutzer als
        # "sieht sehr gut aus" ausgewaehlt). Ab hier (unterhalb von Timer
        # Stop) war Anordnung/Groesse laut Nutzer-Vorgabe bewusst frei.
        # side='bottom' in dieser Reihenfolge gepackt: die zuerst gepackte
        # Version/FPS-Zeile landet ganz unten, danach die Datum/Uhrzeit-
        # Flaeche, danach das Logo obendrauf - macht zusammen mit dem
        # zwischen Buttons und Footer liegenden, nicht explizit gepackten
        # Rest-Platz denselben VPush()-Effekt wie im PySimpleGUI-Original.
        version_row = tk.Frame(left, bg=BG)
        version_row.pack(side='bottom', fill='x')
        tk.Label(version_row, text='V: ' + version, font=('Helvetica', 11),
                 bg=BG, fg=FG_MUTED).pack(side='left', padx=5, pady=(8, 0))
        fps_label = tk.Label(version_row, text='', font=('Helvetica', 11),
                              bg=BG, fg=FG_MUTED, anchor='w')
        fps_label.pack(side='left', padx=5, pady=(8, 0))
        self._reg('-FPS-', fps_label)

        dt_frame = tk.Frame(left, bg=DATETIME_BG)
        dt_frame.pack(side='bottom', fill='x', pady=(0, 10))
        date_label = tk.Label(dt_frame, font=('Courier', 14), bg=DATETIME_BG, fg=NEUTRAL_FG)
        date_label.pack(pady=(8, 0))
        self._reg('-DATE-', date_label)
        time_label = tk.Label(dt_frame, font=('Courier', 52, 'bold'), bg=DATETIME_BG, fg=FG_DARK)
        time_label.pack(pady=(0, 8))
        self._reg('-TIME-', time_label)

        logo_label = tk.Label(left, bg=BG, bd=0)
        logo_label.pack(side='bottom', pady=(0, 12))
        self._reg('-LOGO-', logo_label)

    def _on_video_click(self, event):
        px = event.x / self.video_size[0] * 100
        py = event.y / self.video_size[1] * 100
        self.post('-VIDEO-', {'-VIDEO-': (px, py)})

    def _build_pin_view(self):
        page = self._new_page('-PINVIEW-')
        content = tk.Frame(page, bg=BG, bd=0, highlightthickness=1, highlightbackground=NEUTRAL_BORDER)
        content.place(relx=0.5, rely=0.5, anchor='center')

        # Titeltext wechselt zur Laufzeit zwischen kurzen ("PIN eingeben")
        # und langen Varianten ("Neuen PIN eingeben (4-6 Ziffern)", "PINs
        # stimmen nicht überein") - lag der Titel im selben grid() wie das
        # Tastenfeld, hat ein langer Titel (mit columnspan=3) live am
        # Test-Pi die drei Tastenfeld-Spalten gleichmaessig auseinander-
        # gedrueckt (Tk verteilt fehlende Breite eines spannenden Widgets
        # per Default auf die ueberspannten Spalten). Fix: Tastenfeld in
        # eine EIGENE Frame mit eigenem grid() ausgelagert, dadurch komplett
        # unabhaengig von der Breite des (separat gepackten) Titels - genau
        # das Verhalten, das PySimpleGUIs zeilenbasiertes Layout hier von
        # Haus aus hatte (dort nie ein gemeinsames grid() zwischen Titel und
        # Tastenfeld).
        title = tk.Label(content, text='PIN eingeben', font=('Helvetica', 30), bg=BG, fg=FG_DARK)
        title.pack(padx=30, pady=(20, 10))
        self._reg('-PIN_TITLE-', title)

        display = tk.Label(content, text='', font=('Courier', 40), bg=BG, fg=FG_DARK, width=10, justify='center')
        display.pack(pady=10)
        self._reg('-PINDISPLAY-', display)

        keypad = tk.Frame(content, bg=BG)
        keypad.pack()
        keypad_rows = [('1', '2', '3'), ('4', '5', '6'), ('7', '8', '9')]
        for r, row in enumerate(keypad_rows):
            for c, d in enumerate(row):
                tk.Button(keypad, text=d, width=8, height=4,
                          command=lambda d=d: self.post(d)).grid(row=r, column=c, padx=3, pady=3)
        tk.Button(keypad, text='Löschen', width=8, height=4,
                  command=lambda: self.post('-PIN_CLEAR-')).grid(row=3, column=0, padx=3, pady=3)
        tk.Button(keypad, text='0', width=8, height=4,
                  command=lambda: self.post('0')).grid(row=3, column=1, padx=3, pady=3)
        # OK ist die primaere Aktion des Tastenfelds - Akzentfarbe statt der
        # neutralen Grundoptik (siehe Window.__init__ option_add), damit sie
        # sich sichtbar von den Ziffern/Loeschen abhebt.
        tk.Button(keypad, text='OK', width=8, height=4, bg=ACCENT_ZOOM, fg='white',
                  activebackground=ACCENT_ZOOM, activeforeground='white',
                  command=lambda: self.post('-PIN_OK-')).grid(row=3, column=2, padx=3, pady=3)

        cancel_btn = tk.Button(content, text='Abbrechen', width=26, height=2,
                                command=lambda: self.post('-PIN_CANCEL-'))
        cancel_btn.pack(pady=(5, 20))
        self._reg('-PIN_CANCEL-', cancel_btn, show={'pady': (5, 20)})

    def _build_confirm_view(self):
        page = self._new_page('-CONFIRMVIEW-')
        content = tk.Frame(page, bg=BG, bd=0, highlightthickness=1, highlightbackground=NEUTRAL_BORDER)
        content.place(relx=0.5, rely=0.5, anchor='center')
        tk.Label(content, text='Gerät jetzt neu starten?', font=('Helvetica', 28), bg=BG, fg=FG_DARK).grid(
            row=0, column=0, columnspan=2, padx=30, pady=(30, 15))
        tk.Button(content, text='Ja, neu starten', width=20, height=3, bg=ACCENT_ZOOM, fg='white',
                  activebackground=ACCENT_ZOOM, activeforeground='white',
                  command=lambda: self.post('-CONFIRM_YES-')).grid(row=1, column=0, padx=15, pady=(0, 30))
        tk.Button(content, text='Abbrechen', width=20, height=3,
                  command=lambda: self.post('-CONFIRM_NO-')).grid(row=1, column=1, padx=15, pady=(0, 30))

    def _build_menu_view(self):
        page = self._new_page('-MENUVIEW-')
        content = tk.Frame(page, bg=BG, bd=0, highlightthickness=1, highlightbackground=NEUTRAL_BORDER)
        content.place(relx=0.5, rely=0.5, anchor='center')
        tk.Label(content, text='Einstellungen', font=('Helvetica', 24), bg=BG, fg=FG_DARK).pack(padx=30, pady=(30, 15))
        tk.Button(content, text='Ganze Scheibe', width=24, height=3,
                  command=lambda: self.post('-MENU_FULL-')).pack(pady=5)
        tk.Button(content, text='Innen Scheibe', width=24, height=3,
                  command=lambda: self.post('-MENU_DETAIL-')).pack(pady=5)
        tk.Button(content, text='Stand wechseln', width=24, height=3,
                  command=lambda: self.post('-MENU_STAND-')).pack(pady=5)
        tk.Button(content, text='PIN ändern', width=24, height=3,
                  command=lambda: self.post('-MENU_PIN-')).pack(pady=5)
        # Restart lebt seit dem Header-Redesign (Design-Runde 3) hier statt
        # als eigener Button auf der Hauptseite - der PIN-Schutz besteht
        # weiterhin unveraendert ueber den Settings-Zugang selbst (siehe
        # '-SETTINGS-'-Handler in main()), keine zweite PIN-Abfrage noetig.
        tk.Button(content, text='Neu starten', width=24, height=3,
                  command=lambda: self.post('-MENU_RESTART-')).pack(pady=5)
        tk.Button(content, text='Zurück', width=24, height=2,
                  command=lambda: self.post('-MENU_BACK-')).pack(pady=(5, 30))

    def _build_editor_view(self):
        page = self._new_page('-EDITORVIEW-')
        content = tk.Frame(page, bg=BG, bd=0, highlightthickness=1, highlightbackground=NEUTRAL_BORDER)
        content.place(relx=0.5, rely=0.5, anchor='center')

        title = tk.Label(content, text='', font=('Helvetica', 18), bg=BG, fg=FG_DARK)
        title.pack(pady=(15, 5))
        self._reg('-EDITOR_TITLE-', title)

        canvas = tk.Canvas(content, width=EDITOR_MAX_W, height=EDITOR_MAX_H, bg='black', highlightthickness=0)
        canvas.pack(padx=15)
        canvas.image_refs = []
        canvas.bind('<Button-1>', self._on_editgraph_event)
        canvas.bind('<B1-Motion>', self._on_editgraph_event)
        canvas.bind('<ButtonRelease-1>', lambda e: self.post('-EDITGRAPH-+UP'))
        self.editor_canvas = canvas

        btnrow = tk.Frame(content, bg=BG)
        btnrow.pack(pady=15)
        tk.Button(btnrow, text='Neues Bild', width=13, height=2,
                  command=lambda: self.post('-EDIT_REFRESH-')).pack(side='left', padx=5)
        tk.Button(btnrow, text='Speichern', width=13, height=2, bg=ACCENT_ZOOM, fg='white',
                  activebackground=ACCENT_ZOOM, activeforeground='white',
                  command=lambda: self.post('-EDIT_SAVE-')).pack(side='left', padx=5)
        cancel_btn = tk.Button(btnrow, text='Abbrechen', width=13, height=2,
                                command=lambda: self.post('-EDIT_CANCEL-'))
        cancel_btn.pack(side='left', padx=5)
        self._reg('-EDIT_CANCEL-', cancel_btn, show={'side': 'left', 'padx': 5})

    def _on_editgraph_event(self, event):
        self.post('-EDITGRAPH-', {'-EDITGRAPH-': (event.x, event.y)})

    def _build_stand_view(self):
        page = self._new_page('-STANDVIEW-')
        content = tk.Frame(page, bg=BG, bd=0, highlightthickness=1, highlightbackground=NEUTRAL_BORDER)
        content.place(relx=0.5, rely=0.5, anchor='center')
        tk.Label(content, text='Stand auswählen', font=('Helvetica', 28), bg=BG, fg=FG_DARK).pack(pady=(30, 15))
        listbox = tk.Listbox(content, height=8, width=38, font=('Helvetica', 20),
                              bg=BG, fg=FG_DARK, selectbackground=ACCENT_ZOOM, selectforeground='white',
                              highlightthickness=1, highlightbackground=NEUTRAL_BORDER, bd=0)
        listbox.pack(padx=30)
        self._reg('-STAND_LIST-', listbox)
        btnrow = tk.Frame(content, bg=BG)
        btnrow.pack(pady=(15, 30))
        select_btn = tk.Button(btnrow, text='Auswählen', width=20, height=2, bg=ACCENT_ZOOM, fg='white',
                                activebackground=ACCENT_ZOOM, activeforeground='white',
                                command=lambda: self.post('-STAND_SELECT-'))
        select_btn.pack(side='left', padx=10)
        self._reg('-STAND_SELECT-', select_btn)
        back_btn = tk.Button(btnrow, text='Zurück', width=20, height=2,
                              command=lambda: self.post('-STAND_BACK-'))
        back_btn.pack(side='left', padx=10)
        self._reg('-STAND_BACK-', back_btn, show={'side': 'left', 'padx': 10})

    def _build_camwait_view(self):
        page = self._new_page('-CAMWAITVIEW-')
        content = tk.Frame(page, bg=BG, bd=0, highlightthickness=1, highlightbackground=NEUTRAL_BORDER)
        content.place(relx=0.5, rely=0.5, anchor='center')
        text_label = tk.Label(content, text='', font=('Helvetica', 22), bg=BG, fg=FG_DARK, wraplength=460, justify='center')
        text_label.pack(padx=30, pady=(30, 15))
        self._reg('-CAMWAIT_TEXT-', text_label)
        btnrow = tk.Frame(content, bg=BG)
        btnrow.pack(pady=(0, 30))
        tk.Button(btnrow, text='Erneut versuchen', width=20, height=2, bg=ACCENT_ZOOM, fg='white',
                  activebackground=ACCENT_ZOOM, activeforeground='white',
                  command=lambda: self.post('-CAMWAIT_RETRY-')).pack(side='left', padx=10)
        tk.Button(btnrow, text='Zurück zur Stand-Auswahl', width=24, height=2,
                  command=lambda: self.post('-CAMWAIT_BACK-')).pack(side='left', padx=10)


def _show_page(key):
    window.show_page(key)


def popup(message):
    window.popup(message)


def _set_stand_name(name):
    name = name or ''
    if len(name) > STANDNAME_MAX_CHARS:
        name = name[:STANDNAME_MAX_CHARS - 1] + '…'
    window['-STANDNAME-'].update(name)


def _run_pin_keypad(title, show_cancel, validate):
    # Gemeinsames Tastenfeld-Grundgeruest fuer check_pin() und
    # _enter_new_pin() - beide sammeln Ziffern/-PIN_CLEAR- identisch und
    # unterscheiden sich nur darin, was bei -PIN_OK- als "gueltig" zaehlt
    # und was bei Erfolg zurueckgegeben wird. validate(entered) liefert
    # (True, ergebnis) bei Erfolg (Schleife endet), sonst
    # (False, (fehlertext, sekunden)) - zeigt den Fehlertext rot fuer die
    # angegebene Dauer, dann geht die Eingabe leer weiter. Liefert das
    # Erfolgsergebnis, oder None bei Abbrechen/Fenster zu.
    window['-PIN_TITLE-'].update(title)
    window['-PIN_CANCEL-'].update(visible=show_cancel)
    _show_page('-PINVIEW-')
    window['-PINDISPLAY-'].update('', text_color='black')
    entered = ''
    result = None
    while True:
        event, _ = window.read()
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
                window['-PINDISPLAY-'].update(error_text, text_color='red')
                window.refresh()
                time.sleep(sleep_s)
        elif event in '0123456789':
            if len(entered) < 6:
                entered += event
        window['-PINDISPLAY-'].update('*' * len(entered), text_color='black')
    return result


def check_pin(correct_pin):
    # Generische PIN-Abfrage, schuetzt sowohl Settings als auch Restart.
    # Bewusst keine Sperre nach Fehlversuchen (Nutzer-Entscheidung) - das
    # Bedrohungsmodell ist "zufaelliges Herumtippen vor Ort abschrecken",
    # keine gezielte Brute-Force-Absicherung.
    # Titel/Abbrechen-Sichtbarkeit werden von _run_pin_keypad() explizit
    # auf 'PIN eingeben'/sichtbar zurueckgesetzt - _enter_new_pin()
    # (PIN-Aenderung) aendert beides auf derselben Seite, eine vorherige
    # Aenderung darf hier nicht durchschlagen.
    def validate(entered):
        if entered == str(correct_pin):
            return True, True
        return False, ('falsch', 0.6)
    result = _run_pin_keypad('PIN eingeben', True, validate)
    _show_page('-MAINVIEW-')
    return bool(result)


def confirm_reboot():
    _show_page('-CONFIRMVIEW-')
    event, _ = window.read()
    _show_page('-MAINVIEW-')
    return event == '-CONFIRM_YES-'


def _enter_new_pin(title, show_cancel):
    # Liefert die eingetippte Ziffernfolge (4-6 Stellen, OK gedrueckt)
    # zurueck, oder None (Abbrechen/Fenster zu) - OHNE Vergleich mit einem
    # "richtigen" PIN, anders als check_pin(). Wird sowohl fuer die
    # Neueingabe als auch die Wiederholung genutzt (change_pin_flow ruft
    # diese Funktion zweimal auf).
    def validate(entered):
        if 4 <= len(entered) <= 6:
            return True, entered
        return False, ('4-6 Ziffern', 0.8)
    result = _run_pin_keypad(title, show_cancel, validate)
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
    popup('PIN konnte nicht gespeichert werden.')
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
        if event == WIN_CLOSED:
            return None
        elif event == '-STAND_BACK-' and not forced:
            return None
        elif event == '-STAND_SELECT-' and stands:
            # curselection() statt Werteabgleich per Name - robust auch
            # falls zwei Staende zufaellig denselben Anzeigenamen haben.
            sel = window['-STAND_LIST-'].widget.curselection()
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
        if event in (WIN_CLOSED, '-CAMWAIT_BACK-'):
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
        popup('Kein Kamerabild verfügbar - bitte später erneut versuchen.')
        return None

    img_h, img_w = frame.shape[:2]
    if points is None:
        mx, my = int(img_w * 0.2), int(img_h * 0.2)
        points = [[mx, my], [img_w - mx, my], [mx, img_h - my], [img_w - mx, img_h - my]]
    max_disp_w, max_disp_h = EDITOR_MAX_W, EDITOR_MAX_H
    scale = min(max_disp_w / img_w, max_disp_h / img_h, 1.0)
    disp_w, disp_h = max(1, int(img_w * scale)), max(1, int(img_h * scale))
    # Der Editor-Canvas hat eine feste Groesse (EDITOR_MAX_W x EDITOR_MAX_H -
    # das Layout wird nur einmal beim Programmstart gebaut), das
    # tatsaechliche Kamerabild passt je nach Seitenverhaeltnis meist nicht
    # exakt hinein. off_x/off_y zentrieren das skalierte Bild in diesem
    # Canvas, statt es oben links kleben zu lassen (das erzeugte vorher
    # einen einseitigen schwarzen Rand rechts).
    off_x, off_y = (EDITOR_MAX_W - disp_w) // 2, (EDITOR_MAX_H - disp_h) // 2

    HIT_RADIUS = 35   # grosszuegiger Trefferbereich fuer Finger, in Display-Pixeln
    MAG_SIZE = 220    # Seitenlaenge des Lupen-Overlays in Pixeln
    MAG_SRC = 70       # Seitenlaenge des vergroesserten Kamera-Ausschnitts (Quelle)

    def to_display_frame(f):
        return cv2.resize(f, (disp_w, disp_h)) if scale != 1.0 else f.copy()

    disp_frame = to_display_frame(frame)
    # pts leben ab hier durchgehend in Canvas-Pixelkoordinaten (Bild-
    # Skalierung UND Zentrierungs-Offset bereits eingerechnet) - das
    # entspricht direkt dem Koordinatenraum, den das Canvas-Widget fuer
    # Klicks liefert (event.x/event.y), dadurch ist beim Dragging keine
    # weitere Umrechnung noetig.
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
    graph = window.editor_canvas

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
        # PPM statt PNG: gleiche Begruendung wie bei Window.draw_image() -
        # unkomprimiert ist beim Kodieren billiger, das Ergebnis wird
        # ohnehin sofort wieder dekodiert (kein Speichern/Uebertragen). Der
        # Lupen-Redraw feuert bei jedem Drag-Motion-Event, potenziell
        # mehrfach pro Sekunde.
        magbytes = cv2.imencode('.ppm', mag)[1].tobytes()
        # Fest oben rechts im Canvas verankert (nicht am Bild), damit die
        # Lupe unabhaengig von Bildgroesse/Zentrierung immer an derselben,
        # vorhersehbaren Stelle erscheint.
        mag_x, mag_y = EDITOR_MAX_W - MAG_SIZE - 10, 10
        photo = tk.PhotoImage(data=magbytes)
        graph.image_refs.append(photo)
        graph.create_image(mag_x, mag_y, anchor='nw', image=photo)
        graph.create_rectangle(mag_x, mag_y, mag_x + MAG_SIZE, mag_y + MAG_SIZE, outline='yellow', width=2)

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
            a, b = poly_pts[perimeter[j]], poly_pts[perimeter[(j + 1) % 4]]
            graph.create_line(a[0], a[1], b[0], b[1], fill=color, width=2)

    def redraw(mag_center=None):
        graph.delete('all')
        graph.image_refs = []
        photo = tk.PhotoImage(data=cv2.imencode('.ppm', disp_frame)[1].tobytes())  # PPM statt PNG, s.o.
        graph.image_refs.append(photo)
        graph.create_image(off_x, off_y, anchor='nw', image=photo)
        if other_pts is not None:
            draw_outline(other_pts, '#c0c0c0')
        draw_outline(pts, 'yellow')
        for i, p in enumerate(pts):
            graph.create_oval(p[0] - 10, p[1] - 10, p[0] + 10, p[1] + 10,
                               fill='red', outline='yellow', width=2)
            graph.create_text(p[0], p[1] - 20, text=str(i + 1), fill='yellow',
                               font=('Helvetica', 12, 'bold'))
        if mag_center is not None:
            draw_magnifier(mag_center)

    redraw()
    dragging_idx = None

    while True:
        event, values = window.read()
        if event in (WIN_CLOSED, '-EDIT_CANCEL-'):
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
        if event in (WIN_CLOSED, '-MENU_BACK-'):
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
            popup('Stand konnte nicht gespeichert werden.')
            return False
        elif event == '-MENU_PIN-':
            new_pin = change_pin_flow(current_pin, forced=False)
            _show_page('-MAINVIEW-')
            return new_pin is not None
        elif event == '-MENU_RESTART-':
            # Restart lebt seit dem Header-Redesign hier statt als eigener
            # Hauptbildschirm-Button (siehe _build_menu_view) - der Zugang
            # ist bereits durch den vorgelagerten check_pin() in main()s
            # '-SETTINGS-'-Handler geschuetzt, keine zweite PIN-Abfrage noetig.
            # confirm_reboot() navigiert selbst schon zurueck zur Hauptseite,
            # egal ob bestaetigt oder abgebrochen wurde.
            if confirm_reboot():
                subprocess.run(['/usr/bin/sudo', '/usr/sbin/reboot'])
            return False

    current = section_full if which == 'full' else section_detail
    other = section_detail if which == 'full' else section_full

    result = edit_section_points('Ganze Scheibe' if which == 'full' else 'Innen Scheibe', cap, current, other_points=other)
    _show_page('-MAINVIEW-')
    if result is None:
        return False

    new_section_full = result if which == 'full' else section_full
    new_section_detail = result if which == 'detail' else section_detail

    if not save_sections_override(new_section_full, new_section_detail):
        popup('Speichern fehlgeschlagen - Änderung wurde NICHT übernommen.')
        return False
    return True

def _set_disabled(keys, disable):
    for k in keys:
        window[k].update(disabled=disable)

def _set_icon_buttons(keys, enabled):
    # Analog zu _set_disabled(), aber fuer die farbcodierten Buttons aus
    # Window._make_accent_button(): ein disabled Tk-Button faerbt sich NICHT
    # von selbst um (nur der Text wird blass), das eigentliche "Muted"-Aussehen
    # (helle Flaeche + gedaempftes Icon statt satter Akzentfarbe, siehe
    # Design-Runde 2) wird hier bei jedem Enable/Disable explizit gesetzt.
    for k in keys:
        st = window._btn_style[k]
        w = window[k].widget
        cfg = dict(state=(tk.NORMAL if enabled else tk.DISABLED),
                   bg=st['bg_on'] if enabled else st['bg_off'],
                   fg=st['fg_on'] if enabled else st['fg_off'])
        cfg['activebackground'] = cfg['bg']
        cfg['activeforeground'] = cfg['fg']
        if st['icon_on'] is not None:
            cfg['image'] = st['icon_on'] if enabled else st['icon_off']
        w.config(**cfg)

def zoom_disabled(disable):
  _set_icon_buttons(('-FULL_VIDEO-', '-DETAIL_VIDEO-'), not disable)
  # Reset bleibt dauerhaft im Muted-Zustand (siehe Kommentar bei seiner
  # Erzeugung in _build_main_view) - hier nichts mehr zu tun.

def blink_disabled(disable):
  _set_icon_buttons(('-BLINK_START-',), not disable)
  _set_icon_buttons(('-BLINK_STOP-', '-BLINK_REF-'), False)

def timer_disabled(disable):
  _set_icon_buttons(('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'), not disable)
  _set_icon_buttons(('-TIMER_STOP-',), False)

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

def draw_timer_countdown(frame, seconds_left):
    # Zentriert statt am unteren Rand, etwas groesser als die vorherige
    # Bottom-Left-Platzierung (Nutzer-Wunsch). getTextSize() liefert die
    # tatsaechliche Breite/Hoehe des gerenderten Textes, dadurch klappt die
    # Zentrierung unabhaengig von Ziffernanzahl (1 vs. 2-stellig).
    text = str(seconds_left)
    font = cv2.FONT_HERSHEY_SIMPLEX
    font_scale = 6.5
    thickness = 11
    (tw, th), _ = cv2.getTextSize(text, font, font_scale, thickness)
    x = (frame.shape[1] - tw) // 2
    y = (frame.shape[0] + th) // 2
    cv2.putText(frame, text, (x, y), font, font_scale, (0, 0, 0), thickness, cv2.LINE_AA)


def main():
    VideoSize = (cfg.getProperty('video.size.x'), cfg.getProperty('video.size.y'))

    frame_count = 1
    frame_timestamps = deque()
    FPS_WINDOW_SECONDS = 60
    displayVideo = True
    displayTimer = False
    timerCurrentLoop = 0
    timerStart = datetime.now()
    timerType = ""
    blink = False
    blink_ref = []
    zoom_center = []
    zoom_level = 'full'
    last_frame_id = -1
    last_date_str = None
    last_time_str = None
    global window

    window = Window(cfg, VideoSize)

    #some speed optimisation - avoid searching every frame
    window_date = window['-DATE-']
    window_time = window['-TIME-']
    window_fps = window['-FPS-']

    # Hoehenbasiert statt (wie vor dem Footer-Redesign) breitenbasiert: das
    # Logo im Footer ist jetzt zentriert und bewusst deutlich groesser (Design-
    # Runde 3/4, "eher noch größer... ist ja eine Art CI Branding") -
    # Ausgangsgroesse fuer die Skalierung ist deshalb seine Zielhoehe, die
    # Breite ergibt sich seitenverhaeltnistreu aus dem jeweiligen Logo.
    logo_height = 145
    _show_page('-MAINVIEW-')
    # ressources/logo.png ist bewusst NICHT Teil des Repos (siehe README) -
    # jede Installation legt dort ihr eigenes Logo ab. Fehlt die Datei,
    # bleiben Sidebar-Logo und Blank-Screen-Wasserzeichen einfach leer statt
    # abzustuerzen.
    logo = cv2.imread('ressources/logo.png', cv2.IMREAD_UNCHANGED)
    blank_logo = None
    if logo is not None:
        blank_logo = logo.copy()  # unskalierte Variante fuer den Blank-Screen ("Video aus")
        logo_scale = logo_height / logo.shape[0]
        logo = cv2.resize(logo, (max(1, int(logo.shape[1] * logo_scale)), logo_height))
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
        if event == WIN_CLOSED:
            break
        elif event == '-TOGGLEVIDEO-':
            displayVideo = not displayVideo
            if displayVideo:
              window['-TOGGLEVIDEO-'].widget.config(image=window._icon('eye_slash_neutral'))
              frame_count = 1
              frame_timestamps.clear()
              zoom_disabled(False)
              blink_disabled(False)
              timer_disabled(False)
            else:
              window['-TOGGLEVIDEO-'].widget.config(image=window._icon('eye_neutral'))
              frame = np.zeros((VideoSize[1], VideoSize[0], 3), np.uint8)
              if blank_logo is not None:
                frame = blend_logo_centered(frame, blank_logo)
              window.draw_image(frame)
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
          _set_icon_buttons(('-BLINK_START-',), False)
          _set_icon_buttons(('-BLINK_STOP-', '-BLINK_REF-'), True)
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
        elif event in ('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'):
          # Alle drei Timer-Varianten starten identisch - nur timerType
          # (=event) unterscheidet, welcher Countdown weiter unten
          # gerendert wird. timerCurrentLoop wird auch fuer -TIMER_20-/
          # -TIMER_10- zurueckgesetzt, obwohl nur die 5x3/7-Variante es
          # liest - unschaedlich, vermeidet aber eine dritte fast
          # identische Kopie dieses Blocks.
          zoom_disabled(True)
          blink_disabled(True)
          video_filter_disabled(True)
          displayTimer = True
          _set_icon_buttons(('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'), False)
          _set_icon_buttons(('-TIMER_STOP-',), True)
          displayVideo = False
          timerType = event
          timerStart = datetime.now()
          timerCurrentLoop = 0
        elif event == '-TIMER_STOP-':
          zoom_disabled(False)
          blink_disabled(False)
          video_filter_disabled(False)
          displayTimer = False
          displayVideo = True
          _set_icon_buttons(('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'), True)
          _set_icon_buttons(('-TIMER_STOP-',), False)


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

            window.draw_image(frame)
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
              draw_timer_countdown(frame, prepTime - tmpTimerSecs)
            elif ((tmpTimerSecs - baseTime) < showTime):
              #green
              frame[:] = (0, 255, 0)
              cv2.putText(frame, str(timerCurrentLoop + 1), (20,130), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
              draw_timer_countdown(frame, baseTime + showTime - tmpTimerSecs)
            elif((tmpTimerSecs - baseTime - showTime) < hideTime):
              #red
              frame[:] = (0, 0, 255)
              cv2.putText(frame, str(timerCurrentLoop +1), (20,130), cv2.FONT_HERSHEY_SIMPLEX, 5, (0, 0, 0), 10, cv2.LINE_AA)
              draw_timer_countdown(frame, baseTime + showTime + hideTime - tmpTimerSecs)
            else:
              #red
              if (timerCurrentLoop < loopCounter -1):
                timerCurrentLoop += 1
              else:
                window.post('-TIMER_STOP-')
              frame[:] = (0, 0, 255)
          elif timerType in ("-TIMER_20-", "-TIMER_10-"):
            # -TIMER_20-/-TIMER_10- unterscheiden sich nur in showTime -
            # derselbe einfache Rot-Vorbereitung/Gruen-Countdown/Rot-Stop-
            # Ablauf wie oben, nur ohne die Mehrfach-Wiederholung von
            # -TIMER_5_3_7-.
            showTime = 20 if timerType == "-TIMER_20-" else 10
            if tmpTimerSecs < prepTime:
              #red
              frame[:] = (0, 0, 255)
              draw_timer_countdown(frame, prepTime - tmpTimerSecs)
            elif tmpTimerSecs < (prepTime + showTime):
              #green
              frame[:] = (0, 255, 0)
              draw_timer_countdown(frame, prepTime + showTime - tmpTimerSecs)
            elif tmpTimerSecs < (prepTime + showTime + stopTime):
              #red
              frame[:] = (0, 0, 255)
            else:
              window.post('-TIMER_STOP-')
          window.draw_image(frame)
          window_fps.update('')

        now = datetime.now()
        # Nur bei tatsaechlicher Aenderung neu zeichnen (Datum/Uhrzeit
        # aendern sich hoechstens einmal pro Sekunde, dieser Loop-Tick
        # laeuft aber alle ~10ms) - spart bei rund 99% der Iterationen ein
        # unnoetiges Tk-Redraw dieser beiden Labels.
        date_str = now.strftime("%d.%m.%Y")
        if date_str != last_date_str:
            window_date.update(date_str)
            last_date_str = date_str
        time_str = now.strftime("%H:%M:%S")
        if time_str != last_time_str:
            window_time.update(time_str)
            last_time_str = time_str
    window.close()


if __name__ == '__main__':
    main()
