#!/bin/bash
# Wird von targetdisplay-reboot.service aufgerufen, wenn targetdisplay.service
# wiederholt gescheitert ist (StartLimitBurst erreicht). Rebootet nur bis
# zu einer Obergrenze - bei einem DAUERHAFTEN Fehler (z.B. fehlendes Paket,
# falsche Konfiguration) soll das System nicht fuer immer neu starten,
# sondern anhalten und auf manuelle Untersuchung warten.
#
# Nach demselben Muster wie im oeffentlichen CamDisplay-Repo
# (https://github.com/SGTittmoning/CamDisplay), nur das Namensschema
# angepasst.
#
# Zaehler liegt auf der Boot-Partition (siehe targetdisplay-update.sh fuer
# die Begruendung: uebersteht auch ein aktives Overlay-Root).
#
# WICHTIG: /boot/firmware kann UNABHAENGIG vom Root-Overlay zusaetzlich per
# "bootro" (raspi-config) read-only gemountet sein - das ist der Normalzustand
# in Produktion (siehe targetdisplay-writable.sh). Schreibzugriffe hier muessen
# daher denselben remount-Tanz machen wie targetdisplay-update.sh/-writable.sh,
# sonst schlaegt das Schreiben schlicht fehl, sobald "ro" aktiv ist.
#
# WICHTIG #2 (gefunden 2026-08-28 beim Live-Test gegen den Test-Pi): "[ cond ]
# && cmd" als LETZTE Anweisung einer Funktion/des Skripts ist unter "set -e"
# gefaehrlich - ist "cond" falsch (voellig normaler Fall, kein Fehler), liefert
# das trotzdem Exit-Code 1, und set -e bricht die Funktion/das Skript sofort
# ab, noch VOR dem eigentlichen reboot-Aufruf. Deshalb ueberall ein
# abschliessendes "return 0"/"true", wo dieses Muster als letzte Zeile steht.

set -euo pipefail

BOOT_DIR="/boot/firmware"
COUNT_FILE="$BOOT_DIR/.targetdisplay-reboot-count"
MAX_REBOOTS=5

bootro_now() { raspi-config nonint get_bootro_now; }   # 0=aktiv (ro), 1=inaktiv (rw)

write_count() {
  local was_ro=0
  [ "$(bootro_now)" -eq 0 ] && was_ro=1
  [ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"
  echo "$1" > "$COUNT_FILE"
  [ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
  return 0
}

count=0
if [ -f "$COUNT_FILE" ]; then
  count=$(cat "$COUNT_FILE")
fi
count=$((count + 1))

if [ "$count" -gt "$MAX_REBOOTS" ]; then
  logger -t targetdisplay-reboot-guard "Grenze von $MAX_REBOOTS Reboots erreicht - targetdisplay.service wird deaktiviert statt weiter zu rebooten. Manuelle Pruefung noetig."
  write_count "$count"
  systemctl disable --now targetdisplay.service || true
  exit 0
fi

write_count "$count"
logger -t targetdisplay-reboot-guard "targetdisplay.service wiederholt gescheitert, Reboot $count von $MAX_REBOOTS"
reboot
