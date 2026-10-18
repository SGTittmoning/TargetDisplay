#!/bin/bash
# Ziel: /root/bin/targetdisplay-writable.sh auf dem Raspberry Pi (TargetDisplay)
#
# Schaltet das System zwischen "beschreibbar" (fuer manuelle
# Konfigurationsarbeiten) und "read-only" (OverlayFS + Boot-Partition-RO,
# Normalzustand) um.
#
# Fuer apt-Updates stattdessen targetdisplay-update.sh verwenden (macht
# denselben Umschaltvorgang inkl. apt full-upgrade/autoremove/clean
# automatisch).
# Dieses Script hier ist fuer alles andere: manuelle Config-Edits, neue
# Pakete testen, Log-Dateien inspizieren, etc. - insbesondere wenn gerade
# kein Ansible-Zugriff moeglich ist.
#
# Mechanik, Statusdatei und gemeinsame Funktionen: targetdisplay-overlay-lib.sh.
#
# Nutzung:
#   sudo targetdisplay-writable.sh status   Aktuellen Zustand anzeigen
#   sudo targetdisplay-writable.sh rw       System beschreibbar machen (ggf.
#                                           2x aufrufen, mit einem Reboot
#                                           dazwischen - das Script sagt an,
#                                           was noetig ist)
#   sudo targetdisplay-writable.sh ro       System wieder read-only setzen
#                                           (OverlayFS + Boot-RO), danach
#                                           einmal manuell rebooten. Bricht mit
#                                           Exit 1 ab, wenn der overlayroot-
#                                           Hook im geladenen initramfs auch
#                                           nach Neuerzeugung fehlt.
#
# Setzt aktuelles Raspberry Pi OS voraus (/boot/firmware).

set -euo pipefail

# shellcheck disable=SC2034  # gelesen in targetdisplay-overlay-lib.sh
STATE_FILE="/boot/firmware/.targetdisplay-writable.state"

# shellcheck source=targetdisplay-overlay-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-overlay-lib.sh"

print_status() {
  print_overlay_status "rw-Vorgang-Status:            "
}

cmd_rw() {
  require_root

  if [ "$(overlay_now)" -eq 0 ]; then
    echo "OverlayFS ist aktiv - deaktiviere (wirkt erst nach Reboot)..."
    raspi-config nonint disable_overlayfs
    write_state "awaiting-reboot"
    echo ""
    echo "Bitte jetzt rebooten, danach 'sudo $0 rw' erneut ausfuehren:"
    echo "  sudo reboot"
    return 0
  fi

  echo "OverlayFS ist bereits inaktiv (live)."

  if [ "$(bootro_now)" -eq 0 ]; then
    echo "Deaktiviere Boot-Partition-Schreibschutz (fstab, persistent)..."
    raspi-config nonint disable_bootro
    echo "Mounte Boot-Partition live beschreibbar..."
    mount -o remount,rw "$BOOT_DIR"
  else
    echo "Boot-Partition ist bereits beschreibbar."
  fi

  clear_state
  echo ""
  echo "System ist jetzt vollstaendig beschreibbar (Root + Boot-Partition)."
  echo "Nach Abschluss der Konfigurationsarbeiten: sudo $0 ro"
}

cmd_ro() {
  require_root

  OVERLAY_ALREADY_ACTIVE=0
  [ "$(overlay_now)" -eq 0 ] && OVERLAY_ALREADY_ACTIVE=1

  if [ "$OVERLAY_ALREADY_ACTIVE" -eq 1 ] && [ "$(bootro_now)" -eq 1 ]; then
    echo "Boot-Partition ist noch nicht read-only, OverlayFS aber schon aktiv." >&2
    echo "Eine Aenderung an /etc/fstab waere in diesem Zustand NICHT dauerhaft" >&2
    echo "(landet nur im RAM-Overlay). Bitte zuerst:" >&2
    echo "  sudo $0 rw" >&2
    echo "damit noetigen Reboot durchfuehren, Boot-RO dort setzen, dann" >&2
    echo "erneut 'sudo $0 ro' ausfuehren." >&2
    exit 1
  fi

  if [ "$OVERLAY_ALREADY_ACTIVE" -eq 1 ]; then
    # Overlay laeuft schon: nur den Live-Zustand der Boot-Partition angleichen.
    mount -o remount,ro "$BOOT_DIR"
    clear_state
    echo ""
    echo "OverlayFS war bereits aktiv (laut Kommandozeile). Boot-Partition ist read-only."
    echo "Ob das Overlay wirklich wirkt, zeigt der Mount-Typ unten (erwartet: overlay)."
    print_status
    return 0
  fi

  OVERLAY_RC=0
  restore_readonly || OVERLAY_RC=$?
  if [ "$OVERLAY_RC" -ne 0 ]; then
    exit "$OVERLAY_RC"
  fi

  clear_state
  echo ""
  echo "Fertig. Bitte jetzt rebooten, damit OverlayFS wieder aktiv wird:"
  echo "  sudo reboot"
}

case "${1:-}" in
  status) print_status ;;
  rw)     cmd_rw ;;
  ro)     cmd_ro ;;
  *)
    echo "Usage: $0 {status|rw|ro}" >&2
    exit 1
    ;;
esac
