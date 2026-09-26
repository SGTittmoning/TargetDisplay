"""Tk-Oberflaeche: Widgets, Seiten und Steuerelement-Hilfen."""
import _tkinter
import math
import os
import queue
import time
import tkinter as tk
import tkinter.font as tkfont

import cv2

# Das Fenster wird in main() angelegt und hier abgelegt, damit die Ablauf-Module
# (flows.py) und die Steuerelement-Hilfen unten darauf zugreifen koennen.
window = None


# Feste Canvas-Groesse fuer den Punkte-Editor (edit_section_points): das
# Fenster wird nur EINMAL beim Programmstart gebaut (siehe main()/show_page
# unten - PIN/Settings/Restart sind eigene "Seiten" im selben Fenster, per
# Frame.tkraise() umgeschaltet), das tatsaechliche Kamerabild-
# Seitenverhaeltnis ist zu diesem Zeitpunkt aber noch nicht bekannt. Das
# Bild wird beim Zeichnen einfach oben links in dieser Flaeche platziert
# (siehe to_display_frame/redraw).
EDITOR_MAX_W, EDITOR_MAX_H = 1000, 620


# Maximale Pixelbreite fuer den Standnamen im Header (siehe
# Window.set_stand_name()) - etwas schmaler als die 330px des umgebenden
# Frames (Window._build_main_view(), Frame mit fester width/height +
# pack_propagate(False) als harte Grenze), damit der gekuerzte Text inkl.
# "…" nie ganz an die beiden Header-Icon-Buttons rechts daneben heranreicht.
# Eine Pixelmessung mit der tatsaechlich verwendeten Schrift (Helvetica
# 24pt bold) ist robust gegenueber unterschiedlich breiten Zeichen, ein
# fester Zeichen-Wert waere das nicht. Header ist 450px breit, die beiden
# 46px-Icon-Buttons + 10px Abstand dazwischen brauchen exakt 102px - bis
# dahin sind also 348px fuer den Namen frei, minus etwas Sicherheitsabstand
# zu den Icons (Frame 330px, Kuerzgrenze 310px davon).
STANDNAME_MAX_PX = 310


# Helles Farbschema. Je Funktionsgruppe auf dem Hauptbildschirm
# (Zoom/Blinken/Timer) eine eigene
# Akzentfarbe statt Rahmen zur Unterscheidung; jede Akzentfarbe hat
# zusaetzlich eine abgeschwaechte "Muted"-Variante fuer deaktivierte Buttons
# (statt nur ausgegrautem Text) - siehe _make_accent_button()/
# set_icon_buttons() unten.
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


# Laengste Wartezeit von Window.read() am Stueck (siehe dort).
READ_MAX_WAIT_MS = 200

WIN_CLOSED = '__WIN_CLOSED__'


TIMEOUT_EVENT = '__TIMEOUT__'


class Elem:
    # Duenner Wrapper um ein natives Tk-Widget, der nur die .update(...)-
    # Aufrufmuster abdeckt, die in diesem Skript tatsaechlich vorkommen -
    # ermoeglicht window['-KEY-'].update(...) als einheitliches Zugriffsmuster
    # fuer die State-Machine/das Event-Handling, ohne dass jede Aufrufstelle
    # zwischen den unterschiedlichen nativen Tk-Widget-APIs unterscheiden muss.
    def __init__(self, widget, show=None):
        self.widget = widget
        # show: die exakten pack()-Kwargs, mit denen das Widget sichtbar
        # gemacht wird - fuer die drei Widgets mit visible=-Toggle
        # (-PIN_CANCEL-, -EDIT_CANCEL-, -STAND_BACK-) explizit beim
        # Registrieren mitgegeben, statt sie erst beim ersten Verstecken per
        # w.pack_info() aus Tk zurueckzufragen. Unter Debian Trixie/Python
        # 3.13 kann genau diese pack_info()-Abfrage beim allerersten
        # Verstecken mit "_tkinter.TclError: window ... isn't packed"
        # fehlschlagen, obwohl das Widget bei der Konstruktion nachweislich
        # gepackt wurde (Tcl/Tk-versionsabhaengig). Die pack()-Optionen sind
        # zur Erstellungszeit ohnehin exakt bekannt, eine spaetere
        # Tk-Rueckfrage ist unnoetig und genau die fragile Stelle.
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
    # EIN Tk-Root mit mehreren als Geschwister-Frames angelegten "Seiten"
    # (siehe _PAGE_KEYS), zwischen denen per Frame.tkraise() umgeschaltet
    # wird (siehe show_page). read()/post() bilden ein synchrones,
    # blockierendes Event-Read ueber dem eigentlich asynchronen Tk-Eventloop:
    # jedes Button-Kommando legt sein Event in eine Queue, read() wartet im
    # Tk-Eventloop auf das naechste Tk-Ereignis (schlaeft dabei, statt zu
    # pollen) und liefert das naechste Event (oder nach Ablauf von timeout
    # ein TIMEOUT_EVENT) - dadurch bleibt
    # der Rest der Datei (State-Machine, Event-Handling) eine einfache
    # sequenzielle Schleife statt callback-getriebenem Code.
    def __init__(self, cfg, video_size, version):
        self.video_size = video_size
        self.version = version
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
        # Der Button bleibt bewusst IMMER state=NORMAL - "deaktiviert" wird
        # rein optisch (Muted-Flaeche/-Icon) UND funktional (command wird zu
        # einem No-Op statt self.post(key), s.u.) simuliert, nie ueber Tks
        # eigenes state=DISABLED. Grund: ein tk.Button mit -image zeichnet im
        # disabled-Zustand automatisch ein eingebautes Schachbrett-Stipple-
        # Muster UEBER das Bild (der klassische Motif-"insensitive"-Look,
        # X11-spezifisch) - es gibt dafuer keine abschaltbare Option
        # (-disabledimage existiert bei tk.Button nicht). Das Stipple wuerde
        # wie ein Rendering-Defekt wirken, unabhaengig davon wie sauber
        # PNG/Alpha der Icons selbst sind.
        icon = (icon_on if start_enabled else icon_off) if icon_on is not None else None
        bg = accent_bg if start_enabled else muted_bg
        fg = accent_fg if start_enabled else muted_fg
        real_command = lambda: self.post(key)
        command = real_command if start_enabled else (lambda: None)

        if icon is None:
            # Reiner Text-Button (aktuell nur "Timer Stop") - keine
            # Compound-Bild/Text-Problematik (siehe unten), unveraendert
            # ein einzelnes tk.Button-Widget.
            b = tk.Button(parent, text=text, bg=bg, fg=fg,
                          activebackground=bg, activeforeground=fg,
                          bd=0, relief='flat', highlightthickness=0, font=font,
                          wraplength=140, justify='center', command=command)
            self._btn_style[key] = dict(icon_on=None, icon_off=None, label=None,
                                         containers=(),
                                         bg_on=accent_bg, fg_on=accent_fg,
                                         bg_off=muted_bg, fg_off=muted_fg,
                                         command_on=real_command)
            self._reg(key, b)
            return b

        # Icon+Text-Buttons: Tks eigenes compound='top' (Bild+Text in EINEM
        # Widget) packt zwischen Bild und Text einen deutlich groesseren,
        # nicht konfigurierbaren Abstand als am oberen/unteren Rand des
        # Buttons, unabhaengig von -pady. Bei zweizeilig umbrechenden Labels
        # ("Ganze Scheibe"/"Innen Scheibe") wirkt der Inhalt dadurch sichtbar
        # nach oben verschoben. Deshalb Icon (reiner Bild-Button) und Text
        # (eigenes Label) als zwei gestapelte Widgets mit selbst gewaehltem,
        # gleichmaessigem Abstand statt eines einzelnen Compound-Widgets -
        # dafuer noetig: ._btn_style haelt zusaetzlich das Label, damit
        # set_icon_buttons() dessen bg/fg mit umschalten kann.
        frame = tk.Frame(parent, bg=bg)
        # inner-Frame statt Icon+Label direkt in frame packen: pack_equal()
        # streckt alle Buttons einer Zeile per grid(sticky='nsew') auf die
        # Hoehe des groessten Geschwisters - in der Zoom-Zeile ist das die
        # zweizeilige "Ganze/Innen Scheibe". Ohne inner-Frame haengt der
        # Inhalt oben im (dadurch viel hoeheren) Reset-Frame und sitzt
        # dadurch hoeher als bei einzeiligen Buttons in kuerzeren Zeilen
        # (Start/Stop/Timer). inner.pack(expand=True) zentriert den
        # Icon+Text-Block vertikal im jeweils tatsaechlich zugewiesenen
        # Platz, egal wie hoch die Zeile wird.
        inner = tk.Frame(frame, bg=bg)
        inner.pack(expand=True)
        b = tk.Button(inner, image=icon, bg=bg, activebackground=bg,
                      bd=0, relief='flat', highlightthickness=0,
                      command=command)
        b.pack(side='top', pady=(6, 6))
        label = tk.Label(inner, text=text, bg=bg, fg=fg, font=font,
                          wraplength=140, justify='center')
        label.pack(side='top', pady=(0, 8))
        # Klick soll ueberall auf der Buttonflaeche denselben Effekt wie ein
        # Klick auf das Icon haben - invoke() ruft das GERADE konfigurierte
        # command auf, respektiert also automatisch den enabled/disabled-
        # No-Op-Swap oben. Auf einem Touchscreen muss die GESAMTE farbige
        # Flaeche reagieren, nicht nur Icon (tk.Button) und Textzeile
        # selbst - deshalb bindet auch die umgebende Frame-/inner-Flaeche
        # (der komplette Rand rund um Icon+Text, siehe inner.pack(expand=True)
        # oben) denselben Klick-Handler.
        for w in (frame, inner, label):
            w.bind('<Button-1>', lambda e: b.invoke())

        self._btn_style[key] = dict(icon_on=icon_on, icon_off=icon_off, label=label,
                                     containers=(inner, frame),
                                     bg_on=accent_bg, fg_on=accent_fg,
                                     bg_off=muted_bg, fg_off=muted_fg,
                                     command_on=real_command)
        self._reg(key, b)
        return frame

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
            wait_ms = READ_MAX_WAIT_MS
            if deadline is not None:
                remaining_ms = math.ceil((deadline - time.monotonic()) * 1000)
                if remaining_ms <= 0:
                    self.root.update()
                    return (TIMEOUT_EVENT, {})
                wait_ms = min(wait_ms, remaining_ms)
            # Weckt dooneevent() spaetestens nach wait_ms auf: zum Ablauf von
            # timeout, und damit auch ein aus einem anderen Thread per post()
            # eingereihtes Ereignis bei timeout=None hoechstens READ_MAX_WAIT_MS
            # spaeter gesehen wird.
            wake_id = self.root.after(wait_ms, lambda: None)
            # Blockiert, bis Tk ein Ereignis (Eingabe, Timer, Neuzeichnen)
            # abgearbeitet hat - ohne Wachphasen dazwischen.
            self.root.tk.dooneevent(_tkinter.ALL_EVENTS)
            self.root.after_cancel(wake_id)

    def refresh(self):
        self.root.update()

    def set_stand_name(self, name):
        # Pixelgenaue Kuerzung mit "…" statt einer festen Zeichenanzahl
        # (siehe STANDNAME_MAX_PX oben) - haengt jeweils ein Zeichen ab und
        # prueft per Font.measure() erneut, bis Text+"…" wieder unter das
        # Limit passt. Bei sehr kurzen Namen (passen von vornherein) laeuft
        # die while-Schleife kein einziges Mal.
        if self._standname_font.measure(name) > STANDNAME_MAX_PX:
            while name and self._standname_font.measure(name + '…') > STANDNAME_MAX_PX:
                name = name[:-1]
            name = name + '…'
        self.widgets['-STANDNAME-'].update(name)

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
        # ohne Kompression - das entfaellt hier pro Frame, waehrend PNG bei
        # jedem Frame neu komprimiert werden muesste. itemconfig() statt
        # delete+neu erzeugen vermeidet zusaetzlich jede eigene Buchhaltung
        # ueber das vorherige Canvas-Item.
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
        # 780px Bildhoehe bleiben nur 20px insgesamt, also 10px oben+unten).
        # Der ueberschuessige horizontale Platz landet nicht als reiner
        # rechter Rand, sondern sichtbar als Luecke zwischen Button-Spalte
        # und Video (siehe spacer unten) - dadurch bleibt der Aussenrand auf
        # allen 4 Seiten exakt gleich.
        margin = max(0, (self.screen_size[1] - self.video_size[1]) // 2)

        # Feste 450px-Breite (statt inhaltsbestimmt). pack_propagate(False)
        # erzwingt das hart, die drei Button-Reihen darunter teilen sich
        # diese Breite ueber fill='both'+expand=True gleichmaessig auf
        # (siehe pack_equal()) -
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
        # rechts (Video aus, Settings mit Schloss-Badge) - bewusst zurueck-
        # haltend statt prominenter Textbuttons. Restart ist ausschliesslich
        # ueber Settings -> Menue erreichbar (siehe
        # _build_menu_view/run_settings_flow), kein eigener Button mehr auf
        # der Hauptseite.
        header = tk.Frame(left, bg=BG)
        header.pack(side='top', fill='x', pady=(0, 18))

        # Eine Tk-Breitenangabe in Zeichen (width=...) reicht NICHT: das ist
        # nur eine Mindestbreite, kein Maximum - bei fetter/grosser Schrift
        # wird das Element trotzdem breiter als width= wenn der Inhalt es
        # verlangt. Der Standname kommt aus der frei editierbaren
        # targetdisplay-stands.json, ist also nicht laengenbeschraenkt - ein
        # zu langer Name wuerde sonst das ganze Layout auseinanderdruecken.
        # Ein Frame mit fester width/height + pack_propagate(False) erzwingt
        # dagegen eine wirklich harte Breite - ueberstehender Inhalt wird
        # abgeschnitten statt den Frame zu vergroessern.
        name_frame = tk.Frame(header, width=330, height=38, bg=BG)
        name_frame.pack(side='left')
        name_frame.pack_propagate(False)
        standname_font = ('Helvetica', 24, 'bold')
        name_label = tk.Label(name_frame, text='', font=standname_font,
                               bg=BG, fg=FG_DARK, anchor='w')
        name_label.pack(fill='both', expand=True)
        self._reg('-STANDNAME-', name_label)
        # Fuer die pixelgenaue Kuerzung in set_stand_name() unten - dieselbe
        # Schriftart/-groesse wie name_label, sonst wuerde gegen die falsche
        # Breite gemessen.
        self._standname_font = tkfont.Font(family=standname_font[0], size=standname_font[1],
                                            weight=standname_font[2])

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
        # GROUP_GAP: Abstand zwischen den drei Funktionsgruppen. Jede Gruppe
        # ("Zoom"/"Blinken"/"Timer") traegt statt eines Rahmens nur eine
        # schlichte Grossbuchstaben-Caption in der jeweiligen Akzentfarbe -
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
            # WICHTIG: pack(fill='both', expand=True) macht Geschwister-Widgets
            # NICHT gleich breit - Tk vergibt jedem Button zunaechst seine
            # eigene, inhaltsabhaengige "natuerliche" Breite (laengerer Text
            # = mehr Platz) und verteilt nur den DANACH uebrigen Leerraum
            # gleichmaessig, siehe Tk-Doku zu pack(). grid() mit uniform=
            # erzwingt dagegen ECHTE Gleichverteilung: alle Spalten einer
            # uniform-Gruppe bekommen exakt dieselbe Breite, unabhaengig vom
            # Inhalt. Der Zwischenraum wird bewusst als EIGENE, schmale
            # Spacer-Spalte (fixe minsize=gap, weight=0, NICHT Teil der
            # uniform-Gruppe) zwischen die Button-Spalten gesetzt, statt als
            # padx AM Button - sonst faellt der Randbutton (kein padx an der
            # Aussenseite) breiter aus als die beiden mit Gap-padx.
            parent = buttons[0].master
            col = 0
            for i, b in enumerate(buttons):
                parent.grid_columnconfigure(col, weight=1, uniform='btnrow')
                b.grid(row=0, column=col, sticky='nsew')
                col += 1
                if i < len(buttons) - 1:
                    parent.grid_columnconfigure(col, weight=0, minsize=gap)
                    col += 1

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
        # Reset ist beim Start deaktiviert (Ganze Scheibe, kein manueller Pan -
        # der "Standard"-Zustand) und wird ueber sync_reset_button() unten
        # aktiv/inaktiv geschaltet, sobald sich Zoom-Modus oder Pan-Position
        # davon entfernen bzw. dahin zurueckkehren.
        b3 = self._make_accent_button(zoom_row, '-RESETZOOM-', 'Reset',
                                       ACCENT_ZOOM, 'white', ACCENT_ZOOM_MUTED_BG, ACCENT_ZOOM_MUTED_FG,
                                       icon_on=icon('undo_white'), icon_off=icon('undo_zoom_muted'),
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
        b1 = self._make_accent_button(row1, '-TIMER_5_3_7-', '5 x 3 Sek.',
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
        # Button). Fuer eine exakte Pixelhoehe daher derselbe Kniff wie bei
        # name_frame oben: feste Frame-Hoehe +
        # pack_propagate(False), der Button selbst fuellt sie per fill='both'.
        row2 = tk.Frame(timer_group, bg=BG, height=46)
        row2.pack(side='top', fill='x', pady=(BTN_GAP, 0))
        row2.pack_propagate(False)
        stop_btn = self._make_accent_button(row2, '-TIMER_STOP-', 'Timer Stop',
                                             ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                             start_enabled=False, font=('Helvetica', 14, 'bold'))
        stop_btn.pack(side='left', fill='both', expand=True)

        # -- Footer. Kein Sidebar-Logo: das Vereinswappen bleibt
        # ausschliesslich dem Blank-Screen-Wasserzeichen vorbehalten (siehe
        # blend_logo_centered() bei '-TOGGLEVIDEO-' in main()). side='bottom'
        # in dieser Reihenfolge gepackt: die zuerst gepackte Version/FPS-Zeile
        # landet ganz unten, die Datum/Uhrzeit-Flaeche darueber - macht
        # zusammen mit dem zwischen Buttons und Footer liegenden, nicht
        # explizit gepackten Rest-Platz einen Spacer-Effekt, der den Footer
        # unten haelt statt direkt unter den Buttons.
        version_row = tk.Frame(left, bg=BG)
        version_row.pack(side='bottom', fill='x')
        tk.Label(version_row, text='V: ' + self.version, font=('Helvetica', 11),
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
        # stimmen nicht überein"). Ein gemeinsames grid() von Titel und
        # Tastenfeld wuerde bei einem langen Titel (mit columnspan=3) die
        # drei Tastenfeld-Spalten gleichmaessig auseinanderdruecken (Tk
        # verteilt fehlende Breite eines spannenden Widgets per Default auf
        # die ueberspannten Spalten). Das Tastenfeld liegt deshalb in einer
        # EIGENEN Frame mit eigenem grid(), dadurch komplett unabhaengig von
        # der Breite des (separat gepackten) Titels.
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
        # Restart lebt bewusst nur hier im Menue statt als eigener Button auf
        # der Hauptseite - der PIN-Schutz besteht ueber den Settings-Zugang
        # selbst (siehe '-SETTINGS-'-Handler in main()), eine zweite
        # PIN-Abfrage ist hier nicht noetig.
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
        save_btn = tk.Button(btnrow, text='Speichern', width=13, height=2, bg=ACCENT_ZOOM, fg='white',
                              activebackground=ACCENT_ZOOM, activeforeground='white',
                              command=lambda: self.post('-EDIT_SAVE-'))
        save_btn.pack(side='left', padx=5)
        self._reg('-EDIT_SAVE-', save_btn)
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
        # Heisst bewusst "Abbrechen" wie die anderen Settings-Unteraktionen,
        # nicht "Zurück": dieser Button ist nur sichtbar, wenn forced=False
        # ist (freiwilliger Stand-Wechsel ueber das Settings-Menue - im
        # erzwungenen Ersteinrichtungs-Assistenten bleibt er per
        # visible=not forced versteckt).
        back_btn = tk.Button(btnrow, text='Abbrechen', width=20, height=2,
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


def show_page(key):
    window.show_page(key)


def popup(message):
    window.popup(message)


def set_stand_name(name):
    window.set_stand_name(name or '')


def set_icon_buttons(keys, enabled):
    # Fuer die farbcodierten Buttons aus Window._make_accent_button():
    # bleibt bewusst immer state=NORMAL (siehe
    # Kommentar dort - state=DISABLED wuerde Tks eingebautes Schachbrett-
    # Stipple ueber das Icon zeichnen), "deaktiviert" wird rein durch
    # bg/fg/Icon-Farbe (Muted-Variante) UND einen No-Op-command simuliert.
    for k in keys:
        st = window._btn_style[k]
        w = window[k].widget
        cfg = dict(bg=st['bg_on'] if enabled else st['bg_off'],
                   fg=st['fg_on'] if enabled else st['fg_off'],
                   command=(st['command_on'] if enabled else (lambda: None)))
        cfg['activebackground'] = cfg['bg']
        cfg['activeforeground'] = cfg['fg']
        if st['icon_on'] is not None:
            cfg['image'] = st['icon_on'] if enabled else st['icon_off']
        w.config(**cfg)
        # Icon+Text-Buttons (siehe _make_accent_button()) bestehen aus dem
        # Icon-Button, einem separaten Text-Label und den umgebenden
        # inner-/aussen-Frames (siehe dort - inner zentriert den Inhalt
        # vertikal) - alle muessen beim Umschalten dieselbe bg wie der
        # Button bekommen, sonst bleiben Text/Hintergrund auf der alten
        # Farbe stehen.
        if st['label'] is not None:
            st['label'].config(bg=cfg['bg'], fg=cfg['fg'])
            for c in st['containers']:
                c.config(bg=cfg['bg'])


def zoom_disabled(disable):
  set_icon_buttons(('-FULL_VIDEO-', '-DETAIL_VIDEO-'), not disable)
  # Reset wird hier bewusst NICHT angefasst - waehrend Zoom global deaktiviert
  # ist (Blinken/Timer/Video aus laeuft), muss der Aufrufer zusaetzlich
  # explizit sync_reset_button(..., globally_disabled=True) rufen; beim
  # Zurueckschalten auf verfuegbar entscheidet sync_reset_button() anhand
  # des tatsaechlichen Zoom-Zustands (nicht einfach "wieder an"), siehe dort.


def sync_reset_button(zoom_level, zoom_center, globally_disabled=False):
  # Reset ist nur dann sinnvoll klickbar, wenn der Zoom vom Standard
  # abweicht (nicht "Ganze Scheibe" und/oder ein manueller Pan aktiv).
  # "Reset" selbst muss dabei sowohl einen aktiven manuellen Pan als auch
  # einen aktiven "Innen Scheibe"-Zoom zuruecksetzen - siehe die vier
  # Aufrufstellen unten (Full/Detail/Video-Pan/Reset selbst) sowie an jeder
  # zoom_disabled()-Stelle im Hauptloop.
  enabled = (not globally_disabled) and (zoom_level != 'full' or bool(zoom_center))
  set_icon_buttons(('-RESETZOOM-',), enabled)


def blink_disabled(disable):
  set_icon_buttons(('-BLINK_START-',), not disable)
  set_icon_buttons(('-BLINK_STOP-', '-BLINK_REF-'), False)


def timer_disabled(disable):
  set_icon_buttons(('-TIMER_5_3_7-', '-TIMER_20-', '-TIMER_10-'), not disable)
  set_icon_buttons(('-TIMER_STOP-',), False)


def video_filter_disabled(disable):
  # Bewusst NICHT ueber Elem.update(disabled=...)/state=DISABLED (siehe
  # Kommentar in Window._make_accent_button()) - dieser Button hat ein
  # -image, ein disabled Tk-Button wuerde es mit einem Schachbrett-Stipple
  # ueberzeichnen. Command auf No-Op umschalten hat denselben functionalen
  # Effekt (Klick tut nichts), ohne den Rendering-Fehler.
  window['-TOGGLEVIDEO-'].widget.config(
      command=(lambda: None) if disable else (lambda: window.post('-TOGGLEVIDEO-')))
