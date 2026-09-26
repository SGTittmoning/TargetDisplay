#!/bin/bash
# Wird 5 Minuten nach jedem Boot ausgefuehrt (siehe
# targetdisplay-reboot-count-reset.timer). Der Reboot-Zaehler wird nur
# zurueckgesetzt, wenn targetdisplay.service dann tatsaechlich stabil laeuft:
# aktiv, seit mindestens MIN_STABLE_SEC ohne Unterbrechung und mit weniger
# als MAX_RESTARTS automatischen Neustarts. Ein Dienst, der im Neustart-Zyklus
# haengt (z.B. Kamera dauerhaft ausgefallen), behaelt seinen Zaehlerstand -
# sonst waere die Obergrenze des Reboot-Guards wirkungslos, weil jeder
# Reboot-Zyklus den Zaehler nach 5 Minuten wieder auf 0 setzen wuerde.
# Bei stabilem Lauf hat ein spaeterer, unabhaengiger Fehler wieder die volle
# Anzahl an Reboot-Versuchen zur Verfuegung.
#
# Nach demselben Muster wie im oeffentlichen CamDisplay-Repo
# (https://github.com/SGTittmoning/CamDisplay), nur das Namensschema
# angepasst.
# WICHTIG: /boot/firmware kann per "bootro" read-only gemountet sein,
# unabhaengig vom Root-Overlay - siehe targetdisplay-reboot-guard.sh.
#
# WICHTIG #2 (siehe targetdisplay-reboot-guard.sh fuer Details): "[ cond ]
# && cmd" als letzte Anweisung des Skripts liefert unter
# "set -e" bei falschem cond Exit-Code 1 - hier zwar ohne Folgeschaden
# (nichts laeuft danach mehr), aber der Reset-Service wuerde dadurch bei
# jedem Lauf faelschlich als "failed" erscheinen (systemd wertet den
# Skript-Exitcode des Type=oneshot-Service aus). Deshalb ein explizites
# "exit 0" am Ende.

set -euo pipefail

BOOT_DIR="/boot/firmware"
COUNT_FILE="$BOOT_DIR/.targetdisplay-reboot-count"
SERVICE="targetdisplay.service"
MIN_STABLE_SEC=120
MAX_RESTARTS=3

[ -f "$COUNT_FILE" ] || exit 0

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

bootro_now() { raspi-config nonint get_bootro_now; }

was_ro=0
[ "$(bootro_now)" -eq 0 ] && was_ro=1
[ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"
rm -f "$COUNT_FILE"
[ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
exit 0
