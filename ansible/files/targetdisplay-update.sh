#!/bin/bash
# Wartungs-Update-Zyklus fuer den TargetDisplay-Pi mit aktivem OverlayFS.
#
# Mechanik, Statusdatei und gemeinsame Funktionen: targetdisplay-overlay-lib.sh.
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

set -euo pipefail

STATE_FILE="/boot/firmware/.targetdisplay-update.state"

# shellcheck source=targetdisplay-overlay-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-overlay-lib.sh"

print_status() {
  print_overlay_status "Update-Zyklus-Status:         "
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

  if [ "$(bootro_now)" -eq 0 ]; then
    echo "Deaktiviere Boot-Partition-Schreibschutz (fstab, persistent)..."
    raspi-config nonint disable_bootro
    echo "Mounte Boot-Partition live beschreibbar..."
    mount -o remount,rw "$BOOT_DIR"
  fi

  echo ""
  echo "=== apt update / full-upgrade ==="
  # Scheitert eine Paketquelle (z.B. Uhrzeit falsch, Netz weg), meldet
  # apt-get update das sonst nur als Warnung. Das Update laeuft trotzdem zu
  # Ende, damit Overlay und Schreibschutz wiederhergestellt werden; apply
  # endet dann mit Exit 3.
  UPDATE_INCOMPLETE=0
  apt-get update --error-on=any || UPDATE_INCOMPLETE=1
  DEBIAN_FRONTEND=noninteractive apt-get full-upgrade -y

  echo ""
  echo "=== Bereinigen ==="
  apt-get autoremove -y
  apt-get clean

  echo ""
  OVERLAY_RC=0
  restore_readonly || OVERLAY_RC=$?
  if [ "$OVERLAY_RC" -ne 0 ]; then
    exit "$OVERLAY_RC"
  fi

  clear_state

  echo ""
  echo "Fertig. Bitte jetzt final rebooten, damit OverlayFS/Boot-RO wieder"
  echo "aktiv werden:"
  echo "  sudo reboot"
  if [ "$UPDATE_INCOMPLETE" -eq 1 ]; then
    echo "" >&2
    echo "WARNUNG: apt-get update konnte nicht alle Paketquellen lesen - die Updates sind evtl. unvollstaendig." >&2
    exit 3
  fi
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
