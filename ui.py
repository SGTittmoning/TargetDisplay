"""Tk-Oberflaeche: Widgets, Seiten und Steuerelement-Hilfen."""
import _tkinter
import math
import os
import queue
import time
import tkinter as tk
import tkinter.font as tkfont

import cv2
import PIL.Image
import PIL.ImageTk

import timerlib

# Das Fenster wird in main() angelegt und hier abgelegt, damit die Ablauf-Module
# (flows.py) und die Steuerelement-Hilfen unten darauf zugreifen koennen.
window = None


# Feste Canvas-Groesse fuer den Punkte-Editor. Das Fenster wird einmal beim
# Start gebaut, das Seitenverhaeltnis des Kamerabilds ist dann noch nicht
# bekannt; edit_section_points() skaliert das Bild in diese Flaeche und
# zentriert es.
EDITOR_MAX_W, EDITOR_MAX_H = 1000, 620


# Maximale Breite des Standnamens in Pixeln (siehe set_stand_name()). Der
# Header ist 450 px breit, die beiden Icon-Buttons brauchen 102 px. Der
# Namensrahmen ist 330 px breit; gekuerzt wird ab 310 px, damit das "…"
# Abstand zu den Icons haelt. Gemessen wird mit der echten Schrift, weil
# Zeichen unterschiedlich breit sind.
STANDNAME_MAX_PX = 310


# Abstand zwischen Touch-Buttons (gegen Fehltreffer) und zwischen den
# Gruppen Zoom/Blinken/Timer, in Pixeln.
BTN_GAP = 10
GROUP_GAP = 20

# Helles Farbschema. Jede Funktionsgruppe (Zoom/Blinken/Timer) hat eine
# eigene Akzentfarbe und dazu eine blasse "Muted"-Variante fuer gesperrte
# Buttons (siehe _make_accent_button(), set_icon_buttons()).
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


# Vorgerenderte Icon-PNGs, relativ zu dieser Datei gesucht, damit das
# Arbeitsverzeichnis keine Rolle spielt.
ICON_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ressources', 'icons')


# Laengste Wartezeit von Window.read() am Stueck (siehe dort).
READ_MAX_WAIT_MS = 200

WIN_CLOSED = '__WIN_CLOSED__'


TIMEOUT_EVENT = '__TIMEOUT__'


class Elem:
    # Duenner Wrapper um ein Tk-Widget: window['-KEY-'].update(...) funktioniert
    # fuer alle verwendeten Widget-Arten gleich.
    def __init__(self, widget, show=None):
        self.widget = widget
        # show: pack()-Optionen zum Wiedereinblenden (visible=True). Sie werden
        # beim Registrieren mitgegeben, weil pack_info() unter Debian Trixie/Python
        # 3.13 beim ersten Verstecken mit TclError scheitern kann.
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
    # Ein Tk-Root mit mehreren "Seiten" (Geschwister-Frames, siehe _PAGE_KEYS),
    # umgeschaltet per tkraise() (siehe show_page). Buttons legen ihr Ereignis
    # in eine Queue; read() wartet im Tk-Eventloop und liefert das naechste
    # Ereignis oder nach timeout TIMEOUT_EVENT. So bleibt das Event-Handling in
    # main.py und flows.py eine einfache Schleife statt Callback-Code.
    def __init__(self, cfg, video_size, version):
        self.video_size = video_size
        self.version = version
        self.screen_size = cfg.get_property('screenSize')
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
        # Flache, neutrale Grundoptik fuer alle einfachen Dialog-Buttons ueber die
        # Tk-Optionsdatenbank. Die farbcodierten Buttons und die Header-Icons
        # setzen ihre Farben selbst; config() pro Widget hat Vorrang.
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
        # Der Cache haelt die PhotoImage-Referenzen am Leben; Tk gibt ein Bild
        # frei, sobald keine Python-Referenz mehr existiert.
        img = self.icons.get(name)
        if img is None:
            img = tk.PhotoImage(file=os.path.join(ICON_DIR, name + '.png'))
            self.icons[name] = img
        return img

    def _make_accent_button(self, parent, key, text, accent_bg, accent_fg,
                             muted_bg, muted_fg, icon_on=None, icon_off=None,
                             start_enabled=True):
        # Baustein fuer alle farbcodierten Buttons (Zoom/Blinken/Timer, Timer Stop,
        # Replay). Er bleibt immer state=NORMAL; gesperrt wird optisch (Muted-Farben
        # und -Icon) und durch ein command, das nichts tut. Tk zeichnet bei
        # state=DISABLED ueber ein Bild ein Schachbrettmuster, das sich nicht
        # abschalten laesst.
        icon = icon_on if start_enabled else icon_off
        bg = accent_bg if start_enabled else muted_bg
        fg = accent_fg if start_enabled else muted_fg
        def real_command():
            self.post(key)
        command = real_command if start_enabled else (lambda: None)

        # Icon und Text als zwei Widgets statt compound='top': Tk setzt dort einen
        # grossen, festen Abstand zwischen Bild und Text, zweizeilige Texte sassen
        # sichtbar zu hoch. _btn_style haelt das Label, damit set_icon_buttons()
        # dessen Farben mit umschaltet.
        # Ohne feste Hoehe: die Hoehe ergibt sich aus dem Inhalt. Eine feste
        # Frame-Hoehe fordert Tk bei jedem Farbwechsel (set_icon_buttons) kurz
        # erneut an, das ganze Bedienfeld springt dann.
        frame = tk.Frame(parent, bg=bg)
        # inner zentriert Icon und Text vertikal: pack_equal() streckt alle
        # Buttons einer Reihe auf die Hoehe des hoechsten (zweizeilig "Ganze
        # Scheibe").
        inner = tk.Frame(frame, bg=bg)
        inner.pack(expand=True)
        b = tk.Button(inner, image=icon, bg=bg, activebackground=bg,
                      bd=0, relief='flat', highlightthickness=0,
                      command=command)
        b.pack(side='top', pady=(6, 6))
        label = tk.Label(inner, text=text, bg=bg, fg=fg, font=('Helvetica', 15, 'bold'),
                          wraplength=140, justify='center')
        label.pack(side='top', pady=(0, 8))
        # Die ganze farbige Flaeche reagiert auf Beruehrung, nicht nur Icon und
        # Text. invoke() ruft das aktuelle command auf, im gesperrten Zustand also
        # das, das nichts tut.
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
        # Kuerzt pixelgenau mit "…" (siehe STANDNAME_MAX_PX): ein Zeichen nach dem
        # anderen ab, bis Text und "…" passen.
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
        # frame ist BGR (OpenCV-Konvention), PIL erwartet RGB.
        pil_image = PIL.Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
        if self._video_image is None:
            # Nur beim allerersten Frame: legt das Tk-Bildobjekt UND das
            # zugehoerige Canvas-Item an.
            self._video_image = PIL.ImageTk.PhotoImage(pil_image)
            self._video_image_id = self.video_canvas.create_image(0, 0, anchor='nw', image=self._video_image)
        else:
            # paste() schreibt in das bestehende Tk-Bild, das Canvas-Item zeigt
            # weiter darauf. Deutlich billiger als ein neues PhotoImage pro Bild (Pi 4
            # mit 2560x1920-Stream: etwa 18 statt 30 % eines Kerns).
            self._video_image.paste(pil_image)

    # -- Seiten ------------------------------------------------------------

    def _build_main_view(self):
        page = self._new_page('-MAINVIEW-')

        # Gleicher Aussenabstand auf allen Seiten, aus der Bildhoehe berechnet
        # (800 px Bildschirm, 780 px Bild: 10 px oben und unten). Ueberschuessige
        # Breite landet als Luecke zwischen Button-Spalte und Video (spacer).
        margin = max(0, (self.screen_size[1] - self.video_size[1]) // 2)

        # Feste Breite von 450 px; pack_propagate(False) erzwingt sie. Die
        # Button-Reihen teilen sie gleichmaessig auf (siehe pack_equal()).
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

        # -- Header: Standname links, rechts zwei kleine Icon-Buttons (Video aus,
        # Einstellungen mit Schloss). Neu starten ist nur ueber das
        # Einstellungen-Menue erreichbar.
        header = tk.Frame(left, bg=BG)
        header.pack(side='top', fill='x', pady=(0, 18))

        # Ein Frame mit fester Groesse und pack_propagate(False) begrenzt den
        # Standnamen hart. width= in Zeichen waere nur eine Mindestbreite, ein
        # langer Name aus targetdisplay-stands.json wuerde das Layout sprengen.
        name_frame = tk.Frame(header, width=330, height=38, bg=BG)
        name_frame.pack(side='left')
        name_frame.pack_propagate(False)
        standname_font = ('Helvetica', 24, 'bold')
        name_label = tk.Label(name_frame, text='', font=standname_font,
                               bg=BG, fg=FG_DARK, anchor='w')
        name_label.pack(fill='both', expand=True)
        self._reg('-STANDNAME-', name_label)
        # Dieselbe Schrift wie name_label, fuer die Kuerzung in set_stand_name().
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

        # Statt Rahmen traegt jede Gruppe eine Ueberschrift in ihrer
        # Akzentfarbe, Abstaende siehe BTN_GAP/GROUP_GAP.
        groups = tk.Frame(left, bg=BG)
        groups.pack(side='top', fill='x')

        def make_caption(parent, text, color):
            tk.Label(parent, text=text.upper(), font=('Helvetica', 13, 'bold'),
                     bg=BG, fg=color, anchor='w').pack(side='top', anchor='w', pady=(0, 7))

        def pack_equal(buttons, gap=BTN_GAP):
            # Gleich breite Buttons per grid(uniform=): pack(expand=True) verteilt
            # nur den Restplatz gleich, laengere Texte bekaemen breitere Buttons. Der
            # Abstand ist eine eigene Spalte (minsize=gap, nicht in der uniform-Gruppe);
            # padx am Button machte die Randbuttons breiter als den mittleren.
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
        # Reset ist beim Start gesperrt (Ganze Scheibe, kein manueller Ausschnitt)
        # und wird ueber sync_reset_button() geschaltet.
        b3 = self._make_accent_button(zoom_row, '-RESETZOOM-', 'Reset',
                                       ACCENT_ZOOM, 'white', ACCENT_ZOOM_MUTED_BG, ACCENT_ZOOM_MUTED_FG,
                                       icon_on=icon('undo_white'), icon_off=icon('undo_zoom_muted'),
                                       start_enabled=False)
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
        pack_equal([b1, b2, b3])

        # Timer
        timer_group = tk.Frame(groups, bg=BG)
        timer_group.pack(side='top', fill='x')
        make_caption(timer_group, 'Timer', ACCENT_TIMER)
        row1 = tk.Frame(timer_group, bg=BG)
        row1.pack(side='top', fill='x')
        pack_equal([self._make_accent_button(row1, key, timerlib.TITLES[key],
                                             ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                             icon_on=icon('clock_white'), icon_off=icon('clock_timer_muted'))
                    for key in timerlib.TIMER_KEYS])
        # Zweite Reihe: "Timer Stop" ueber zwei Spalten und ein einzelner
        # Replay-Button (siehe replay.py), der waehrend des Replays zu
        # "Replay Ende" wird (siehe replay_buttons()). Spalten und Abstaende
        # wie in den Reihen darueber, damit die Kanten fluchten.
        row2 = tk.Frame(timer_group, bg=BG)
        row2.pack(side='top', fill='x', pady=(BTN_GAP, 0))
        stop_btn = self._make_accent_button(row2, '-TIMER_STOP-', 'Timer Stop',
                                             ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                             icon_on=icon('stopsquare_white'), icon_off=icon('stopsquare_timer_muted'),
                                             start_enabled=False)
        replay_btn = self._make_accent_button(row2, '-REPLAY-', 'Replay',
                                               ACCENT_TIMER, 'white', ACCENT_TIMER_MUTED_BG, ACCENT_TIMER_MUTED_FG,
                                               icon_on=icon('play_white'), icon_off=icon('play_timer_muted'),
                                               start_enabled=False)
        for col in (0, 2, 4):
            row2.grid_columnconfigure(col, weight=1, uniform='btnrow')
        for col in (1, 3):
            row2.grid_columnconfigure(col, weight=0, minsize=BTN_GAP)
        stop_btn.grid(row=0, column=0, columnspan=3, sticky='nsew')
        replay_btn.grid(row=0, column=4, sticky='nsew')

        # -- Footer, von unten gepackt: Version/FPS ganz unten, darueber Datum und
        # Uhrzeit. Der freie Platz zwischen Buttons und Footer haelt ihn unten.
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

        # Das Tastenfeld hat ein eigenes grid() in einem eigenen Frame: in einem
        # gemeinsamen grid() mit dem Titel wuerde ein langer Titel ("Neuen PIN
        # eingeben (4-6 Ziffern)") die Tastenspalten auseinanderdruecken.
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
        # OK in Akzentfarbe, damit es sich von Ziffern und Loeschen abhebt.
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
        # Neu starten nur hier: der PIN fuer die Einstellungen schuetzt auch
        # diese Aktion, eine zweite PIN-Abfrage ist nicht noetig.
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
        # Heisst "Abbrechen" wie in den anderen Unterdialogen. Nur beim freiwilligen
        # Stand-Wechsel sichtbar, im Assistenten versteckt.
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
    # Sperrt oder gibt farbcodierte Buttons frei, ueber Farben, Icon und ein
    # command, das nichts tut (siehe _make_accent_button()).
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
        # Label und beide Frames bekommen dieselbe Hintergrundfarbe wie der
        # Button, sonst blieben sie auf der alten Farbe.
        if st['label'] is not None:
            st['label'].config(bg=cfg['bg'], fg=cfg['fg'])
            for c in st['containers']:
                c.config(bg=cfg['bg'])


def apply_controls(mode, blink=False, zoom_level='full', zoom_center=(), has_recording=False):
    """Setzt alle Buttons des Hauptbildschirms passend zum Zustand.

    mode: 'live' (Kamerabild), 'blank' (Video aus), 'timer' oder 'replay'.
    Alle Sperren stehen hier an einer Stelle; main.py ruft das nach jedem
    Bedienereignis auf.
    """
    live, replay = mode == 'live', mode == 'replay'
    zoom = live or replay
    set_icon_buttons(('-FULL_VIDEO-', '-DETAIL_VIDEO-'), zoom)
    # Reset nur, wenn der Zoom vom Standard abweicht (Innen Scheibe oder
    # manueller Ausschnitt).
    set_icon_buttons(('-RESETZOOM-',), zoom and (zoom_level != 'full' or bool(zoom_center)))
    # Blinken nur im Livebild; laeuft es, sind Referenz und Stop bedienbar
    # statt Start. Die Timer bleiben auch beim Blinken startbar.
    set_icon_buttons(('-BLINK_START-',), live and not blink)
    set_icon_buttons(('-BLINK_REF-', '-BLINK_STOP-'), live and blink)
    set_icon_buttons(timerlib.TIMER_KEYS, live)
    set_icon_buttons(('-TIMER_STOP-',), mode == 'timer')
    _replay_button(available=(live and has_recording) or replay, active=replay)
    # Video aus: im Livebild (nicht beim Blinken), Video ein: bei Video aus.
    _video_toggle(enabled=(live and not blink) or mode == 'blank', video_on=mode != 'blank')


def _replay_button(available, active):
    # Ein Button fuer beides: ausser im Replay "Replay" (startet es), im
    # Replay "Replay Ende" (zurueck zum Livebild).
    st = window._btn_style['-REPLAY-']
    st['label'].config(text='Replay Ende' if active else 'Replay')
    st['icon_on'] = window._icon('exit_white' if active else 'play_white')
    st['icon_off'] = window._icon('exit_timer_muted' if active else 'play_timer_muted')
    st['command_on'] = (lambda: window.post('-REPLAY_END-')) if active else (lambda: window.post('-REPLAY-'))
    set_icon_buttons(('-REPLAY-',), available)


def _video_toggle(enabled, video_on):
    # Das Symbol zeigt, was ein Klick bewirkt: Auge mit Schraegstrich bei
    # laufendem Video. Gesperrt nicht ueber state=DISABLED (der Button hat ein
    # Bild, siehe _make_accent_button()), sondern ueber ein command, das
    # nichts tut.
    window['-TOGGLEVIDEO-'].widget.config(
        image=window._icon('eye_slash_neutral' if video_on else 'eye_neutral'),
        command=(lambda: window.post('-TOGGLEVIDEO-')) if enabled else (lambda: None))
