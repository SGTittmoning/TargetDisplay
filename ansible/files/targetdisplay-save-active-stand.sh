#!/bin/bash
# Wird von main.py (Ersteinrichtungs-Assistent / spaeterer Stand-Wechsel im
# Settings-Menue) per sudo aufgerufen, um die Stand-Auswahl fuer DIESES
# Geraet persistent zu speichern - main.py laeuft als unprivilegierter
# targetdisplay-User und kann /boot/firmware nicht selbst beschreibbar
# machen. Ueber sudoers auf genau dieses eine Skript eingegrenzt (siehe
# tasks/settings_sudo.yml).
#
# Liegt bewusst auf der Boot-Partition, nicht in config.yml auf dem
# Root-FS - siehe targetdisplay-save-sections.sh fuer die ausfuehrliche
# Begruendung (Root-Overlay braeuchte sonst zwei Reboots statt eines
# Remounts).
#
# Nimmt das neue JSON ({"id": "..."}) auf STDIN entgegen. Bewusst kein
# Parsing/Validieren der Stand-ID gegen targetdisplay-stands.json hier -
# main.py prueft das vor dem Aufruf, dieses Skript bleibt so simpel wie
# moeglich (kleinere Angriffsflaeche fuer das sudoers-Recht).
#
# Schreibt atomar (temp-Datei + mv statt direktem "cat >"): "mv" innerhalb
# derselben Partition ist ein einzelner, unteilbarer Verzeichnis-Eintrag-
# Wechsel - ein Stromausfall waehrend main.py hier hineinschreibt trifft so
# entweder die alte, vollstaendige Datei oder gar keine, nie eine
# angebrochene/kaputte. Wichtig, weil main.py das Ergebnis direkt danach
# ungeprueft parsen wuerde.
#
# WICHTIG: "[ cond ] && cmd" als LETZTE Anweisung einer Funktion ist unter
# "set -e" gefaehrlich (siehe targetdisplay-reboot-guard.sh fuer die
# ausfuehrliche Erklaerung) - deshalb ueberall ein abschliessendes
# "return 0"/"exit 0".

set -euo pipefail

BOOT_DIR="/boot/firmware"
TARGET="$BOOT_DIR/targetdisplay-active-stand.json"
TMP="$TARGET.tmp"

bootro_now() { raspi-config nonint get_bootro_now; }   # 0=aktiv (ro), 1=inaktiv (rw)

was_ro=0
[ "$(bootro_now)" -eq 0 ] && was_ro=1
[ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"

cat > "$TMP"
mv "$TMP" "$TARGET"

[ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
exit 0
