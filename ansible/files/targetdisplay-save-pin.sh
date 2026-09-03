#!/bin/bash
# Wird von main.py (PIN-Aenderung im Settings-Menue, freiwillig oder im
# Ersteinrichtungs-Assistenten erzwungen solange der PIN noch auf dem
# Standardwert "1234" steht) per sudo aufgerufen, um den geaenderten PIN
# fuer DIESES Geraet persistent zu speichern. Bewusst ein EIGENES Skript
# statt targetdisplay-save-active-stand.sh/-save-sections.sh mitzunutzen -
# jede Funktion bekommt ihr eigenes, engst moegliches Sudo-Recht (siehe
# tasks/settings_sudo.yml), ein kompromittierter main.py-Prozess kann so
# nie mehr als die eine Aktion ausloesen, fuer die gerade ein Aufruf
# tatsaechlich noetig ist.
#
# Nimmt das neue JSON ({"pin": "..."}) auf STDIN entgegen. Bewusst kein
# Parsing/Validieren hier - main.py validiert (Format, Laenge) vor dem
# Aufruf, dieses Skript bleibt so simpel wie moeglich.
#
# Schreibt atomar (temp-Datei + mv statt direktem "cat >") - siehe
# targetdisplay-save-active-stand.sh fuer die Begruendung.
#
# WICHTIG: "[ cond ] && cmd" als LETZTE Anweisung einer Funktion ist unter
# "set -e" gefaehrlich (siehe targetdisplay-reboot-guard.sh fuer die
# ausfuehrliche Erklaerung) - deshalb ueberall ein abschliessendes
# "return 0"/"exit 0".

set -euo pipefail

BOOT_DIR="/boot/firmware"
TARGET="$BOOT_DIR/targetdisplay-pin.json"
TMP="$TARGET.tmp"

bootro_now() { raspi-config nonint get_bootro_now; }   # 0=aktiv (ro), 1=inaktiv (rw)

was_ro=0
[ "$(bootro_now)" -eq 0 ] && was_ro=1
[ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"

cat > "$TMP"
mv "$TMP" "$TARGET"

[ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
exit 0
