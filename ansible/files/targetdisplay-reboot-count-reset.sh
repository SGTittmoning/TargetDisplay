#!/bin/bash
# Wird 5 Minuten nach jedem Boot ausgefuehrt (siehe
# targetdisplay-reboot-count-reset.timer). Wenn das System so lange laeuft,
# ohne dass targetdisplay-reboot-guard.sh erneut zugeschlagen hat, gilt der
# Zustand als stabil - der Reboot-Zaehler wird zurueckgesetzt, damit ein
# spaeterer, unabhaengiger Fehler wieder die volle Anzahl an Reboot-Versuchen
# zur Verfuegung hat.
#
# Nach demselben Muster wie im oeffentlichen CamDisplay-Repo
# (https://github.com/SGTittmoning/CamDisplay), nur das Namensschema
# angepasst.
# WICHTIG: /boot/firmware kann per "bootro" read-only gemountet sein,
# unabhaengig vom Root-Overlay - siehe targetdisplay-reboot-guard.sh.
#
# WICHTIG #2 (gefunden 2026-08-28, siehe targetdisplay-reboot-guard.sh fuer
# Details): "[ cond ] && cmd" als letzte Anweisung des Skripts liefert unter
# "set -e" bei falschem cond Exit-Code 1 - hier zwar ohne Folgeschaden
# (nichts laeuft danach mehr), aber der Reset-Service wuerde dadurch bei
# jedem Lauf faelschlich als "failed" erscheinen (systemd wertet den
# Skript-Exitcode des Type=oneshot-Service aus). Deshalb ein explizites
# "exit 0" am Ende.

set -euo pipefail

BOOT_DIR="/boot/firmware"
COUNT_FILE="$BOOT_DIR/.targetdisplay-reboot-count"

[ -f "$COUNT_FILE" ] || exit 0

bootro_now() { raspi-config nonint get_bootro_now; }

was_ro=0
[ "$(bootro_now)" -eq 0 ] && was_ro=1
[ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"
rm -f "$COUNT_FILE"
[ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
exit 0
