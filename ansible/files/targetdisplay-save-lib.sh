#!/bin/bash
# Gemeinsame Funktion der drei targetdisplay-save-*.sh-Skripte (Ausschnitte,
# Stand-Auswahl, PIN). Wird von ihnen per "source" eingebunden und ist
# selbst nicht per sudo aufrufbar (siehe tasks/settings_sudo.yml).
#
# save_boot_json <dateiname>: nimmt ein JSON-Dokument auf STDIN entgegen und
# legt es als /boot/firmware/<dateiname> ab.
#
# - Die Eingabe wird zuerst in eine Datei im Arbeitsspeicher gelesen, auf
#   MAX_BYTES begrenzt und als JSON geprueft. Erst danach wird die Boot-
#   Partition beschreibbar gemacht: ein kompromittierter main.py-Prozess kann
#   damit weder die kleine FAT-Partition vollschreiben noch ungueltige Dateien
#   ablegen, die den naechsten Start stoeren.
# - Geschrieben wird atomar (temp-Datei + mv statt direktem "cat >").
# - Die Boot-Partition wird ueber einen EXIT-Trap in JEDEM Fall (auch bei
#   Fehler, z.B. voller Partition) wieder read-only gesetzt, sofern sie es
#   vorher war.
#
# WICHTIG: "[ cond ] && cmd" als LETZTE Anweisung einer Funktion ist unter
# "set -e" gefaehrlich (siehe targetdisplay-reboot-guard.sh) - deshalb
# stehen die Pruefungen hier als if-Bloecke.

set -euo pipefail

BOOT_DIR="/boot/firmware"
MAX_BYTES=65536

_save_payload=""
_save_tmp=""
_save_was_ro=0

_save_cleanup() {
  [ -z "$_save_payload" ] || rm -f "$_save_payload"
  if [ "$_save_was_ro" -eq 1 ]; then
    [ -z "$_save_tmp" ] || rm -f "$_save_tmp"
    mount -o remount,ro "$BOOT_DIR"
  fi
  return 0
}

save_boot_json() {
  local name="$1"
  local target="$BOOT_DIR/$name"
  _save_tmp="$target.tmp"
  trap _save_cleanup EXIT

  _save_payload=$(mktemp)
  head -c $((MAX_BYTES + 1)) > "$_save_payload"
  if [ "$(stat -c %s "$_save_payload")" -gt "$MAX_BYTES" ]; then
    echo "Eingabe ist groesser als $MAX_BYTES Bytes - abgebrochen." >&2
    exit 1
  fi
  if ! python3 -c 'import json, sys; json.load(open(sys.argv[1]))' "$_save_payload" 2>/dev/null; then
    echo "Eingabe ist kein gueltiges JSON - abgebrochen." >&2
    exit 1
  fi

  # 0=aktiv (ro), 1=inaktiv (rw)
  if [ "$(raspi-config nonint get_bootro_now)" -eq 0 ]; then
    _save_was_ro=1
    mount -o remount,rw "$BOOT_DIR"
  fi

  cp "$_save_payload" "$_save_tmp"
  mv "$_save_tmp" "$target"
  return 0
}
