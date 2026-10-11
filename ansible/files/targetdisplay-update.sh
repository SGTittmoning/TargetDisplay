#!/bin/bash
# Wartungs-Update-Zyklus fuer den TargetDisplay-Pi mit aktivem OverlayFS.
#
# OverlayFS (raspi-config) macht das Root-Dateisystem nur zur Boot-Zeit
# beschreibbar/unbeschreibbar (Kernel-Cmdline-Parameter) - ein Toggle wirkt
# erst nach einem Reboot. Die Boot-Partition (/boot/firmware) dagegen kann
# live per remount umgeschaltet werden, die persistente fstab-Einstellung
# (raspi-config disable_bootro/enable_bootro) verweigert aber die Arbeit,
# solange das Root-Overlay live aktiv ist - deshalb der zweiphasige Ablauf
# mit genau einem Zwischen-Reboot.
#
# Nutzung:
#   sudo targetdisplay-update.sh status   Aktuellen Zustand anzeigen
#   sudo targetdisplay-update.sh begin    Phase 1: OverlayFS deaktivieren
#                                         -> danach manuell rebooten
#   sudo targetdisplay-update.sh apply    Phase 2 (nach dem Reboot): Boot-RO
#                                         deaktivieren, Updates installieren,
#                                         bereinigen, Boot-RO + OverlayFS
#                                         wieder aktivieren -> danach nochmal
#                                         manuell rebooten
#
# Nach demselben Muster wie im oeffentlichen CamDisplay-Repo:
# https://github.com/SGTittmoning/CamDisplay
#
# WICHTIG (siehe targetdisplay-reboot-guard.sh fuer Details): "[ cond ] &&
# cmd" als LETZTE Anweisung einer Funktion liefert
# unter "set -e" bei falschem cond Exit-Code 1 - und da write_state()/
# clear_state() unten als blanke Anweisungen (nicht in if/||) aufgerufen
# werden, bricht das den jeweiligen Aufrufer sofort ab, sobald die
# Boot-Partition beim Aufruf NICHT bereits read-only ist. Besonders
# kritisch bei clear_state() in cmd_apply(): das haette die abschliessenden
# "Fertig, bitte final rebooten"-Hinweise verschluckt. Deshalb ueberall ein
# abschliessendes "return 0".

set -euo pipefail

BOOT_DIR="/boot/firmware"
# WICHTIG: State-Datei liegt bewusst auf der Boot-Partition, nicht unter
# /var/lib (Root-Dateisystem)! Wenn OverlayFS beim Aufruf von 'begin' noch
# aktiv ist, landet jeder Schreibzugriff auf das Root-FS im fluechtigen
# tmpfs-Upper-Layer und ist nach dem noetigen Zwischen-Reboot wieder weg.
# /boot/firmware wird vom Overlay nie erfasst (overlayroot behandelt vfat als
# "fs-unsupported"), bleibt also ueber den Reboot hinweg garantiert
# erhalten.
STATE_FILE="$BOOT_DIR/.targetdisplay-update.state"

overlay_now() { raspi-config nonint get_overlay_now; }   # 0=aktiv, 1=inaktiv
bootro_now()  { raspi-config nonint get_bootro_now; }     # 0=aktiv, 1=inaktiv

require_root() {
  if [ "$(id -u)" -ne 0 ]; then
    echo "Bitte mit sudo ausfuehren." >&2
    exit 1
  fi
}

write_state() {
  local was_ro=0
  [ "$(bootro_now)" -eq 0 ] && was_ro=1
  [ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"
  echo "$1" > "$STATE_FILE"
  [ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
  return 0
}

clear_state() {
  [ -f "$STATE_FILE" ] || return 0
  local was_ro=0
  [ "$(bootro_now)" -eq 0 ] && was_ro=1
  [ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"
  rm -f "$STATE_FILE"
  [ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
  return 0
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

print_status() {
  echo "OverlayFS laut Kommandozeile: $([ "$(overlay_now)" -eq 0 ] && echo ja || echo nein)"
  echo "Root-Dateisystem (Mount-Typ):   $(findmnt -n -o FSTYPE / 2>/dev/null || echo unbekannt)  (wirksam nur bei: overlay)"
  echo "Boot-Partition read-only:    $([ "$(bootro_now)" -eq 0 ] && echo ja || echo nein)"
  if [ -f "$STATE_FILE" ]; then
    echo "Update-Zyklus-Status:         $(cat "$STATE_FILE")"
  else
    echo "Update-Zyklus-Status:         idle"
  fi
  if systemctl is-active --quiet targetdisplay.service; then
    echo "Hinweis: targetdisplay.service laeuft aktiv - waehrend der beiden Reboots ist die Scheiben-Anzeige kurz unterbrochen."
  fi
}

cmd_begin() {
  require_root
  if [ -f "$STATE_FILE" ]; then
    echo "Es laeuft bereits ein Update-Zyklus (Status: $(cat "$STATE_FILE"))." >&2
    echo "Bitte erst mit 'apply' abschliessen, oder $STATE_FILE manuell entfernen." >&2
    exit 1
  fi

  echo "--- Zustand vor Aenderung ---"
  print_status
  echo ""

  echo "Deaktiviere OverlayFS (wirkt erst nach Reboot)..."
  raspi-config nonint disable_overlayfs

  if [ "$(overlay_now)" -eq 1 ]; then
    write_state "awaiting-apply-no-reboot"
    echo ""
    echo "OverlayFS war bereits inaktiv (live) - kein Zwischenreboot noetig."
    echo "Weiter mit: sudo $0 apply"
  else
    write_state "awaiting-reboot-1"
    echo ""
    echo "Bitte jetzt rebooten, danach 'sudo $0 apply' ausfuehren:"
    echo "  sudo reboot"
  fi
}

cmd_apply() {
  require_root
  if [ ! -f "$STATE_FILE" ]; then
    echo "Kein laufender Update-Zyklus gefunden. Bitte zuerst 'sudo $0 begin' ausfuehren." >&2
    exit 1
  fi
  ST=$(cat "$STATE_FILE")
  if [ "$ST" != "awaiting-reboot-1" ] && [ "$ST" != "awaiting-apply-no-reboot" ]; then
    echo "Unerwarteter Zustand '$ST' in $STATE_FILE. Bitte manuell pruefen." >&2
    exit 1
  fi
  if [ "$(overlay_now)" -eq 0 ]; then
    echo "OverlayFS ist noch aktiv (live) - Phase 1 ist noch nicht durch einen Reboot wirksam geworden." >&2
    echo "Bitte zuerst rebooten, dann diesen Befehl erneut ausfuehren." >&2
    exit 1
  fi

  echo "OverlayFS ist inaktiv (live), fahre fort."

  BOOTRO_WAS_ACTIVE=0
  if [ "$(bootro_now)" -eq 0 ]; then
    BOOTRO_WAS_ACTIVE=1
    echo "Deaktiviere Boot-Partition-Schreibschutz (fstab, persistent)..."
    raspi-config nonint disable_bootro
    echo "Mounte Boot-Partition live beschreibbar..."
    mount -o remount,rw "$BOOT_DIR"
  fi

  echo ""
  echo "=== apt update / full-upgrade ==="
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y

  echo ""
  echo "=== Bereinigen ==="
  apt-get autoremove -y
  apt-get clean

  if [ "$BOOTRO_WAS_ACTIVE" -eq 1 ]; then
    echo ""
    echo "Setze Boot-Partition auf read-only (fstab)..."
    raspi-config nonint enable_bootro
  fi

  # Overlay aktivieren und initramfs-Hook pruefen, solange die Boot-Partition
  # noch live beschreibbar ist (siehe enable_overlay_checked); erst danach
  # live read-only mounten.
  echo ""
  echo "Aktiviere OverlayFS wieder (wirkt erst nach Reboot)..."
  OVERLAY_RC=0
  enable_overlay_checked || OVERLAY_RC=$?
  if [ "$BOOTRO_WAS_ACTIVE" -eq 1 ]; then
    mount -o remount,ro "$BOOT_DIR"
  fi
  if [ "$OVERLAY_RC" -ne 0 ]; then
    exit "$OVERLAY_RC"
  fi

  clear_state

  echo ""
  echo "Fertig. Bitte jetzt final rebooten, damit OverlayFS/Boot-RO wieder"
  echo "aktiv werden:"
  echo "  sudo reboot"
}

case "${1:-}" in
  status) print_status ;;
  begin)  cmd_begin ;;
  apply)  cmd_apply ;;
  *)
    echo "Usage: $0 {status|begin|apply}" >&2
    exit 1
    ;;
esac
