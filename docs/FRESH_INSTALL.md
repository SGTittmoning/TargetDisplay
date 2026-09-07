# Fresh Install — kompletter Neuaufbau eines Stands per Ansible

Schritt-für-Schritt-Anleitung für die Ersteinrichtung eines frisch
geflashten Raspberry Pi (Raspberry Pi OS Lite, 64-bit) als TargetDisplay-
Stand — **komplett über Ansible, ohne interaktiven Login auf dem Gerät**,
bis auf den einen unten explizit markierten manuellen Schritt.

Diese Anleitung ergänzt `ansible/README.md` (dort steht das *Referenz*-
Wissen zu jeder einzelnen Ansible-Rolle/Task) um eine konkrete, in der
Reihenfolge abarbeitbare Checkliste für den Fall "Gerät ist komplett leer,
ich fange bei null an".

Diese Anleitung ist end-to-end gegen einen frisch geflashten Pi (Raspberry
Pi OS Lite, Debian 13/Trixie, aarch64) lauffähig: Nach einem vollständigen
Durchlauf ist `targetdisplay.service` aktiv, der Ersteinrichtungs-Assistent
zeigt alle konfigurierten Stände korrekt an, und ein hinterlegtes
Vereinslogo wird korrekt eingespielt.

## Voraussetzungen

- Der Pi ist mit Raspberry Pi OS Lite (64-bit) frisch geflasht und im
  Netzwerk erreichbar (z.B. per Raspberry Pi Imager mit vorkonfiguriertem
  Hostname/SSH-Key/WLAN direkt beim Flashen).
- Ein SSH-Key des Ansible-Control-Rechners ist als `authorized_key` für
  einen Admin-User auf dem Pi hinterlegt (ebenfalls direkt beim Flashen
  einrichtbar, oder nachträglich per `ssh-copy-id`).
- Dieser Admin-User ist Mitglied der `sudo`-Gruppe (Standard bei einem via
  Raspberry Pi Imager angelegten Benutzer).
- Auf dem Control-Rechner: `ansible-core`, `git`, Zugriff auf das
  TargetDisplay-Repo, sowie die Collection `community.general` (liefert das
  `timezone`-Modul, das `tasks/base.yml` verwendet — **nicht** Teil von
  `ansible.builtin`, ein reiner `ansible-core`-Minimal-Setup hat es nicht
  automatisch dabei):

  ```bash
  ansible-galaxy collection install community.general
  ```

### Control-Rechner ist ein Mac

Alles oben gilt unverändert (die Playbooks laufen auf dem *verwalteten*
Pi, nicht auf dem Mac) — nur die Installation von Ansible selbst
unterscheidet sich:

```bash
brew install ansible
```

Installiert damit direkt das volle `ansible`-Paket (nicht nur
`ansible-core`) inkl. `community.general` — der oben beschriebene
zusätzliche `ansible-galaxy collection install`-Schritt entfällt dann.
Wer stattdessen bewusst nur `ansible-core` will (z.B. um wie in diesem
Projekt genutzt Lücken zwischen `ansible.builtin` und den Collections
sichtbar zu halten, siehe Kommentar oben): `python3 -m pip install --user
ansible-core`, dann weiterhin den `ansible-galaxy collection
install community.general`-Schritt ausführen. In beiden Fällen wird kein
Xcode/Homebrew-Zubehör auf dem *Pi* installiert — nur `git`,
`ansible-playbook`, ein SSH-Key und (für den optionalen Boot-Overrides-
Kniff im Anhang) der Finder zum Mounten der frisch geflashten SD-Karte
werden auf dem Mac gebraucht.

## Schritt 0 — Manueller Vorbereitungsschritt: Passwortloses sudo

**Das ist der einzige Schritt in dieser Anleitung, der nicht über Ansible
laufen kann.** Ansible braucht für praktisch jeden Task Root-Rechte
(`become: yes` in `install.yml`) — ohne passwortloses sudo würde jeder Lauf
interaktiv nach dem sudo-Passwort fragen, was einem unbeaufsichtigten
Ansible-Lauf entgegensteht. Da Ansible selbst noch kein sudo hat, kann es
sich dieses Recht nicht selbst einrichten (Henne-Ei-Problem) — dieser eine
Schritt muss also einmalig interaktiv mit dem Passwort des Admin-Users
ausgeführt werden, direkt am Gerät oder per SSH:

```bash
ssh <admin-user>@<pi-host-oder-ip>
echo "<admin-user> ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/<admin-user>-nopasswd
sudo chmod 0440 /etc/sudoers.d/<admin-user>-nopasswd
sudo visudo -c
```

`visudo -c` prüft am Ende die Syntax **aller** sudoers-Dateien. Meldet das
einen Fehler: in der laufenden SSH-Sitzung bleiben (dort funktioniert sudo
ja noch) und die Datei korrigieren/löschen, bevor man sich ausloggt — sonst
im schlimmsten Fall kein sudo mehr auf dem Gerät möglich.

Ab hier läuft alles Weitere unattended über Ansible, kein Login auf dem
Gerät mehr nötig.

## Schritt 1 — Ansible-Control-Rechner vorbereiten

```bash
git clone <targetdisplay-git-repo-url>
cd TargetDisplay/ansible
cp inventory.yml.dist inventory.yml
```

`inventory.yml` ist bewusst `.gitignore`t (enthält Kamera-Zugangsdaten) —
sie existiert nur lokal auf dem Control-Rechner.

## Schritt 2 — `inventory.yml` ausfüllen

Drei Dinge müssen rein:

1. **Host-Eintrag** für den Pi (Hostname/IP + der Admin-User aus Schritt 0):

   ```yaml
   hosts:
     stand1.example.lan:
       ansible_host: 192.168.x.x
       ansible_user: <admin-user>
       # optional, siehe unten:
       # my_settings_pin: "1234"
   ```

2. **`targetdisplay_stands`** — die flottenweite Liste aller Stände
   (Name + Kamera-RTMP-URL je Stand). Identisch an jeden Host der Gruppe
   ausgerollt; welchen konkreten Stand ein Gerät zeigt, wird **nicht**
   hier festgelegt, sondern einmalig am Gerät selbst im
   Ersteinrichtungs-Assistenten gewählt (siehe `ansible/README.md`,
   Abschnitt "First-run setup wizard"). Ein neuer Stand kommt also nur
   einmal hier rein und ist danach auf jedem Gerät der Flotte wählbar,
   ohne erneuten Playbook-Lauf pro Gerät.

3. **`targetdisplay_git_repo`** — die Repo-URL, aus der die App selbst
   ausgecheckt wird (inkl. Zugangsdaten, falls privat).

`my_settings_pin` ist optional — fehlt sie, startet das Gerät mit dem
eingebauten Standard-PIN `1234` und erzwingt eine PIN-Änderung im
Ersteinrichtungs-Assistenten, bevor es in den Normalbetrieb geht.

## Schritt 3 — Eigenes Vereinslogo hinterlegen (optional)

Ohne diesen Schritt zeigt das Gerät das generische Platzhalter-Wappen aus
dem Repo. Für das echte Vereinslogo:

```bash
cp /pfad/zum/logo.png ansible/files/logo.png
```

- Muss `logo.png` heißen, liegt auf dem Control-Rechner (nicht im Repo,
  `.gitignore`t), RGBA/Transparenz wird unterstützt.
- Wird bei **jedem** `install.yml`-Lauf automatisch auf jeden Stand
  überschrieben — kein manuelles Kopieren pro Gerät. Datei einfach wieder
  löschen, um beim nächsten Lauf zum Platzhalter zurückzufallen.

## Schritt 4 — Playbook laufen lassen

```bash
ansible-playbook -i inventory.yml install.yml --limit stand1.example.lan
```

Das erledigt in einem Rutsch (siehe `ansible/README.md` für Details je
Punkt): System-Update, alle benötigten Pakete (X11, Matchbox, Python/
OpenCV-Stack), dedizierter unprivilegierter `targetdisplay`-Service-User,
Zeitzone/NTP, persistentes Journal-Logging, feste Display-Auflösung
(1280×800), App-Checkout in eine venv, Rendern von `config.yml` +
`targetdisplay-stands.json`, Logo-Overlay, systemd-Service für die X11-
Session, eng gefasstes sudo für die drei PIN-geschützten Settings-Aktionen
der App selbst, und den Reboot-Guard (Auto-Reboot nach wiederholtem
Camera-Stream-Ausfall).

Mehrere `reboot`-Schritte sind Teil des Playbooks (HDMI-Konfiguration,
Kernel-Updates, journald-Umstellung) — Ansible wartet diese automatisch ab,
kein manuelles Eingreifen nötig. Ein kompletter Lauf gegen ein frisches
Image dauert dadurch spürbar länger als ein reiner Update-Lauf.

Eine Warnung ist dabei normal und unschädlich: `Module remote_tmp
/home/targetdisplay/.ansible/tmp did not exist and was created with a mode
of 0700` — passiert beim allerersten `become_user`-Wechsel auf den frisch
angelegten `targetdisplay`-User, der Task läuft trotzdem korrekt durch.

## Schritt 5 — Verifizieren

Nach erfolgreichem Lauf (`failed=0` in der Ansible-Zusammenfassung):

```bash
ssh <admin-user>@<pi-host> "systemctl is-active targetdisplay.service"
```

sollte `active` liefern. Das Gerät zeigt jetzt entweder den
Ersteinrichtungs-Assistenten (frisches Gerät: Stand wählen, beide Bereiche
kalibrieren, PIN setzen) oder direkt den Kiosk-Bildschirm, falls
`targetdisplay-stands.json` bereits einen vorausgewählten Stand enthält.

**Touch-Kalibrierung ist noch Handarbeit** (kein Ansible-Task dafür, siehe
`ansible/README.md`, Abschnitt "Touch calibration"): `xinput_calibrator`
auf dem Gerät ausführen und die Werte in `ansible/files/99-calibration.conf`
(bzw. eine Host-spezifische Kopie) eintragen, **bevor** das Overlay
aktiviert wird (Schritt 6) — danach ist das Root-Dateisystem read-only.

## Schritt 6 — Optional: Read-only-Filesystem aktivieren

Erst nachdem Bild und Touch bestätigt funktionieren:

```bash
ansible-playbook -i inventory.yml install.yml --limit stand1.example.lan -e targetdisplay_enable_overlay=true
```

Schützt gegen SD-Karten-Korruption bei hartem Stromausfall (die Stände
werden nicht sauber heruntergefahren, sondern an der Wand ausgeschaltet).
Danach sind weitere Config-Änderungen nur noch über den in
`ansible/README.md` beschriebenen rw/ro-Tanz (`maintain.yml` bzw.
`targetdisplay-writable.sh` direkt auf dem Gerät) möglich.

## Zusammenfassung: was bleibt manuell?

| Schritt | Warum nicht automatisierbar |
|---|---|
| Schritt 0: Passwortloses sudo | Henne-Ei-Problem — Ansible braucht sudo, um sudo einzurichten |
| Schritt 5: Touch-Kalibrierung | Pro physischem Touchscreen unterschiedliche Werte, kein generischer Ansible-Task dafür vorhanden |

Alles andere — System-Setup, App-Deployment, Stand-Konfiguration, Logo,
Display-Auflösung, Hardening — läuft vollständig über `install.yml`, ohne
Login auf dem Gerät.

## Anhang: mehrere Stände auf einmal, Wizard pro Gerät überspringen

Für eine komplette Flotten-Neuinstallation (z.B. alle 5 Stände auf einmal
neu geflasht) zwei zusätzliche Kniffe, die den Ablauf oben ergänzen, aber
nicht ersetzen:

- **Alle Hosts der Inventory-Gruppe auf einmal**: `--limit` einfach
  weglassen — `ansible-playbook -i inventory.yml install.yml` installiert
  dann jeden Host unter `hosts:` nacheinander.
- **Ersteinrichtungs-Assistent pro Gerät überspringen, wenn schon bekannt
  ist, welcher Stand wo steht**: `targetdisplay-active-stand.json`
  (`{"id": "standN"}`) und `targetdisplay-sections.json`
  (`{"section_full": [...], "section_detail": [...]}`, aus einer
  bestehenden Kalibrierung exportiert) lassen sich **vor dem ersten Boot**
  direkt auf die frisch geflashte Boot-Partition kopieren (FAT-Partition,
  gleiche Ebene wie `config.txt` — z.B. am Mac unter `/Volumes/bootfs/`
  nach dem Flashen, vor dem Auswerfen der Karte). Der Assistent überspringt
  dann Stand-Auswahl und Eckpunkt-Kalibrierung automatisch und verlangt nur
  noch die einmalige PIN-Änderung direkt am Gerät (aus Sicherheitsgründen
  bewusst nicht vorbelegbar, siehe `main.py::change_pin_flow`). Nützlich,
  wenn ein Gerät exakt dieselbe Position/Kameraausrichtung wie zuvor
  bekommt; bei jeder Abweichung lieber den Assistenten regulär durchlaufen
  lassen statt eine falsche Kalibrierung vorzubelegen.
