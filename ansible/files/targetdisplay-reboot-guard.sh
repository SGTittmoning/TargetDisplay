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
# WICHTIG #2: "[ cond ] && cmd" als LETZTE Anweisung einer Funktion/des
# Skripts ist unter "set -e" gefaehrlich - ist "cond" falsch (voellig
# normaler Fall, kein Fehler), liefert das trotzdem Exit-Code 1, und set -e
# bricht die Funktion/das Skript sofort ab, noch VOR dem eigentlichen
# reboot-Aufruf. Deshalb ueberall ein abschliessendes "return 0"/"true", wo
# dieses Muster als letzte Zeile steht.

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
  count=$(cat "$COUNT_FILE" 2>/dev/null || true)
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
