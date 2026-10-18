#!/bin/bash
# Gemeinsame Funktion der drei targetdisplay-save-*.sh-Skripte (Ausschnitte,
# Stand-Auswahl, PIN). Wird von ihnen per "source" eingebunden und ist
# selbst nicht per sudo aufrufbar (siehe tasks/settings_sudo.yml).
#
# Die App laeuft als unprivilegierter Benutzer und ruft je Aktion ein eigenes
# Skript per sudo auf: ein kompromittierter App-Prozess kann so nur genau
# diese Aktionen ausloesen. Die Werte liegen auf der Boot-Partition statt in
# config.yml: sie laesst sich live umschalten, das Root-Overlay nur per
# Reboot. Die App liest beim Start config.yml und danach diese Dateien.
# Inhaltlich (Format, Werte) prueft die App vor dem Aufruf.
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
# - Die Boot-Partition wird in jedem Fall (auch bei Fehler, z.B. voller
#   Partition) wieder read-only gesetzt, sofern sie es vorher war
#   (with_boot_rw, targetdisplay-boot-lib.sh).
#
# WICHTIG: "[ cond ] && cmd" als LETZTE Anweisung einer Funktion ist unter
# "set -e" gefaehrlich (siehe targetdisplay-reboot-guard.sh) - deshalb
# stehen die Pruefungen hier als if-Bloecke.

set -euo pipefail

# shellcheck source=targetdisplay-boot-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-boot-lib.sh"

MAX_BYTES=65536

_save_payload=""

_save_cleanup() {
  [ -z "$_save_payload" ] || rm -f "$_save_payload"
  return 0
}

# Atomar schreiben; nach einem Fehler bleibt keine temp-Datei liegen.
_save_write() {
  local target="$1"
  if cp "$_save_payload" "$target.tmp" && mv "$target.tmp" "$target"; then
    return 0
  fi
  rm -f "$target.tmp"
  return 1
}

save_boot_json() {
  local target="$BOOT_DIR/$1"
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

  with_boot_rw _save_write "$target"
}
