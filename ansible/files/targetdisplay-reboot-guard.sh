#!/bin/bash
# Wird von targetdisplay-reboot.service aufgerufen, wenn targetdisplay.service
# wiederholt gescheitert ist (StartLimitBurst erreicht). Rebootet nur bis
# zu einer Obergrenze (MAX_REBOOTS) - bei einem DAUERHAFTEN Fehler (z.B.
# Kamera ausgefallen, falsche Konfiguration) soll das System nicht fuer
# immer neu starten.
#
# Der Dienst wird dabei NIE deaktiviert: Die Staende laufen unbeaufsichtigt
# und ohne Zugriff von aussen, eine dauerhafte Abschaltung waere dort nicht
# mehr behebbar. Ist die Obergrenze erreicht, bleibt der Dienst im Zustand
# "failed" und wird von targetdisplay-retry.timer in festen Abstaenden
# erneut gestartet (siehe targetdisplay-retry.sh) - sobald die Ursache
# (z.B. die Kamera) behoben ist, kommt der Stand von selbst wieder.
#
# Der Zaehler liegt auf der Boot-Partition: sie uebersteht auch ein aktives
# Overlay-Root (Schreiben siehe targetdisplay-boot-lib.sh).

set -euo pipefail

# shellcheck source=targetdisplay-boot-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-boot-lib.sh"

MAX_REBOOTS=5

_write_count_file() { echo "$1" > "$REBOOT_COUNT_FILE"; }
write_count() { with_boot_rw _write_count_file "$1"; }

count=0
if [ -f "$REBOOT_COUNT_FILE" ]; then
  count=$(cat "$REBOOT_COUNT_FILE" 2>/dev/null || true)
fi
# Eine leere oder beschaedigte Zaehlerdatei (z.B. nach Stromausfall waehrend
# des Schreibens) zaehlt als 0; sonst braeche die Rechnung unten unter
# "set -e" ab, ohne Reboot und ohne Log-Eintrag.
case "$count" in
  ''|*[!0-9]*)
    logger -t targetdisplay-reboot-guard "Zaehlerdatei unlesbar, zaehle ab 0"
    count=0
    ;;
esac
previous=$count
count=$((count + 1))

if [ "$count" -gt "$MAX_REBOOTS" ]; then
  logger -t targetdisplay-reboot-guard "Grenze von $MAX_REBOOTS Reboots erreicht - kein weiterer Reboot. targetdisplay.service bleibt aktiviert und wird von targetdisplay-retry.timer periodisch neu gestartet."
  # Der Zaehler wird nur beim Ueberschreiten der Grenze einmal geschrieben.
  # Danach feuert OnFailure= mit jedem Retry-Zyklus erneut; ein weiteres
  # Hochzaehlen wuerde die FAT-Boot-Partition unbegrenzt beschreiben, ohne
  # dass der Wert noch etwas aussagt (der Reset-Timer loescht die Datei).
  if [ "$previous" -le "$MAX_REBOOTS" ]; then
    write_count "$count"
  fi
  exit 0
fi

write_count "$count"
logger -t targetdisplay-reboot-guard "targetdisplay.service wiederholt gescheitert, Reboot $count von $MAX_REBOOTS"
reboot
