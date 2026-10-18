#!/bin/bash
# Wird 5 Minuten nach jedem Boot und danach alle 10 Minuten ausgefuehrt
# (siehe targetdisplay-reboot-count-reset.timer). Der Reboot-Zaehler wird nur
# zurueckgesetzt, wenn targetdisplay.service dann tatsaechlich stabil laeuft:
# aktiv, seit mindestens MIN_STABLE_SEC ohne Unterbrechung und mit weniger
# als MAX_RESTARTS automatischen Neustarts. Ein Dienst, der im Neustart-Zyklus
# haengt (z.B. Kamera dauerhaft ausgefallen), behaelt seinen Zaehlerstand -
# sonst waere die Obergrenze des Reboot-Guards wirkungslos, weil jeder
# Reboot-Zyklus den Zaehler nach 5 Minuten wieder auf 0 setzen wuerde.
# Bei stabilem Lauf hat ein spaeterer, unabhaengiger Fehler wieder die volle
# Anzahl an Reboot-Versuchen zur Verfuegung.
#
# Schreiben auf die Boot-Partition: targetdisplay-boot-lib.sh.

set -euo pipefail

# shellcheck source=targetdisplay-boot-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-boot-lib.sh"

SERVICE="targetdisplay.service"
MIN_STABLE_SEC=120
MAX_RESTARTS=3

[ -f "$REBOOT_COUNT_FILE" ] || exit 0

if ! systemctl is-active --quiet "$SERVICE"; then
  logger -t targetdisplay-reboot-count-reset "$SERVICE laeuft nicht - Reboot-Zaehler bleibt bestehen."
  exit 0
fi

restarts=$(systemctl show -p NRestarts --value "$SERVICE")
if [ "${restarts:-0}" -ge "$MAX_RESTARTS" ]; then
  logger -t targetdisplay-reboot-count-reset "$SERVICE wurde ${restarts}x automatisch neu gestartet - Reboot-Zaehler bleibt bestehen."
  exit 0
fi

# Beide Werte in Mikrosekunden seit Systemstart (monoton, unabhaengig von
# Zeitspruengen der Systemuhr).
active_since_us=$(systemctl show -p ActiveEnterTimestampMonotonic --value "$SERVICE")
uptime_us=$(awk '{printf "%d", $1 * 1000000}' /proc/uptime)
stable_sec=$(( (uptime_us - ${active_since_us:-0}) / 1000000 ))
if [ "$stable_sec" -lt "$MIN_STABLE_SEC" ]; then
  logger -t targetdisplay-reboot-count-reset "$SERVICE laeuft erst seit ${stable_sec}s (< ${MIN_STABLE_SEC}s) - Reboot-Zaehler bleibt bestehen."
  exit 0
fi

with_boot_rw rm -f "$REBOOT_COUNT_FILE"
exit 0
