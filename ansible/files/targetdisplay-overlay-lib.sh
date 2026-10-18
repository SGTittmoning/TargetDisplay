#!/bin/bash
# Gemeinsame Funktionen von targetdisplay-writable.sh und
# targetdisplay-update.sh. Wird von beiden per "source" eingebunden; vorher
# setzt das Skript STATE_FILE.
#
# Mechanik:
# - Das Root-OverlayFS ist ein Boot-Zeit-Mechanismus (Kernel-Cmdline). Ein
#   Umschalten wirkt erst nach einem Neustart.
# - Die Boot-Partition (/boot/firmware) laesst sich live per remount
#   umschalten. raspi-configs disable_bootro/enable_bootro (persistente
#   fstab-Einstellung) verweigern aber die Arbeit, solange das Overlay live
#   aktiv ist; daher die Ablaeufe mit Zwischen-Neustart.
# - STATE_FILE liegt auf der Boot-Partition: das Overlay erfasst sie nie,
#   ein Schreibzugriff auf das Root-FS waere dagegen nach dem Neustart weg.
#
# WICHTIG: "[ cond ] && cmd" als LETZTE Anweisung einer Funktion liefert
# unter "set -e" bei falschem cond Exit-Code 1. Werden solche Funktionen als
# blanke Anweisung (nicht in if/||) aufgerufen, bricht das den Aufrufer ab
# (siehe targetdisplay-reboot-guard.sh). Deshalb enden die Funktionen hier
# mit "return 0", die beiden Skripte verweisen nur hierher.

# shellcheck source=targetdisplay-boot-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-boot-lib.sh"

overlay_now() { raspi-config nonint get_overlay_now; }   # 0=aktiv, 1=inaktiv

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausfuehren." >&2
    exit 1
  fi
}

_write_state_file() { echo "$1" > "$STATE_FILE"; }
write_state() { with_boot_rw _write_state_file "$1"; }

clear_state() {
  [ -f "$STATE_FILE" ] || return 0
  with_boot_rw rm -f "$STATE_FILE"
}

# Gibt die initramfs-Dateien auf der Boot-Partition aus, denen der
# overlayroot-Hook fehlt (leer: alle in Ordnung oder lsinitramfs fehlt).
overlay_initramfs_missing() {
  local f n out=""
  command -v lsinitramfs >/dev/null 2>&1 || return 0
  for f in "$BOOT_DIR"/initramfs*; do
    [ -e "$f" ] || continue
    n=$(lsinitramfs "$f" 2>/dev/null | grep -c overlayroot) || true
    [ "${n:-0}" -gt 0 ] || out="$out $f"
  done
  echo "$out"
}

# Overlay aktivieren und sicherstellen, dass das von der Firmware geladene
# initramfs den overlayroot-Hook wirklich enthaelt. "overlayroot=tmpfs" in der
# Kommandozeile allein macht kein Overlay - den Mount erledigt ein Skript IM
# initramfs. Ist /boot/firmware dabei schon read-only gemountet, erzeugt apt das
# neue initrd zwar, der Kopierschritt nach /boot/firmware scheitert aber
# lautlos; das Root-FS bleibt beschreibbar, get_overlay_now meldet trotzdem
# "aktiv". Deshalb: Aufruf NUR bei live beschreibbarer Boot-Partition, danach
# Hook pruefen und bei Bedarf neu erzeugen. Rueckgabe 1, wenn der Hook auch
# danach fehlt (die Boot-Partition setzt der Aufrufer in jedem Fall zurueck).
enable_overlay_checked() {
  raspi-config nonint enable_overlayfs
  if [ -n "$(overlay_initramfs_missing)" ]; then
    echo "initramfs ohne overlayroot-Hook - erzeuge neu..."
    update-initramfs -u -k all
    if [ -n "$(overlay_initramfs_missing)" ]; then
      echo "FEHLER: overlayroot-Hook fehlt in$(overlay_initramfs_missing) - das Overlay waere nach dem Reboot NICHT wirksam." >&2
      return 1
    fi
  fi
  return 0
}

# Status von Overlay und Boot-Partition, dazu der Vorgangsstatus aus
# STATE_FILE; $1 ist die Beschriftung samt Doppelpunkt und Einrueckung.
print_overlay_status() {
  echo "OverlayFS laut Kommandozeile: $([ "$(overlay_now)" -eq 0 ] && echo ja || echo nein)"
  echo "Root-Dateisystem (Mount-Typ):   $(findmnt -n -o FSTYPE / 2>/dev/null || echo unbekannt)  (wirksam nur bei: overlay)"
  echo "Boot-Partition read-only:    $([ "$(bootro_now)" -eq 0 ] && echo ja || echo nein)"
  if [ -f "$STATE_FILE" ]; then
    echo "$1$(cat "$STATE_FILE")"
  else
    echo "${1}idle"
  fi
}
