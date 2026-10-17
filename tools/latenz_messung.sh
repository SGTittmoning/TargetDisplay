#!/bin/bash
# Misst die Verzoegerung des Kamera-Streams ueber die Uhrzeit im Kamerabild
# (siehe latenz_messung.py). Die App wird waehrend der Messung angehalten,
# damit der Pi den Stream nicht doppelt dekodiert, und danach in jedem Fall
# wieder gestartet - auch bei Abbruch (Strg+C) oder getrennter
# SSH-/VPN-Verbindung.
#
# Aufruf: sudo ./latenz_messung.sh [Sekunden]   (Standard 300)
#
# Ein gestoppter Dienst steht auf "inactive": Restart=always greift dabei
# nicht, und targetdisplay-retry.timer startet nur einen Dienst neu, der auf
# "failed" steht. Der Reboot-Guard reagiert ebenfalls nur auf "failed".

set -euo pipefail

DAUER="${1:-300}"
SERVICE=targetdisplay.service
APP_DIR=/home/targetdisplay/TargetDisplay
SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
OUT="/tmp/latenz-$(hostname)-$(date +%Y%m%d-%H%M%S)"

if [ "$(id -u)" -ne 0 ]; then
  echo "Bitte mit sudo starten." >&2
  exit 1
fi

# Getrennte SSH-/VPN-Verbindung: weiterlaufen, Ausgabe landet weiter im
# Protokoll (ignorierte Signale erben auch tee und python).
trap '' HUP
mkdir -p "$OUT"
exec > >(tee --output-error=warn -a "$OUT/protokoll.txt") 2>&1

echo "Messung $(date '+%F %T'), Dauer ${DAUER} s, Ergebnis in ${OUT}"
NTP_SYNC="$(timedatectl show -p NTPSynchronized --value)"
echo "Pi-Uhr per NTP synchron: ${NTP_SYNC}"
if [ "$NTP_SYNC" != "yes" ]; then
  echo "WARNUNG: Pi-Uhr nicht synchron - das Ergebnis taugt nur fuer den Nachkommaanteil, wenn ueberhaupt."
fi
timedatectl timesync-status 2>/dev/null | grep -E 'Server|Offset|Jitter' || true

WAS_ACTIVE="$(systemctl is-active "$SERVICE" || true)"
restore() {
  if [ "$WAS_ACTIVE" = "active" ]; then
    echo "Starte ${SERVICE} wieder."
    systemctl start "$SERVICE"
  fi
  if [ -d "$OUT" ]; then
    tar czf "${OUT}.tar.gz" -C "$(dirname "$OUT")" "$(basename "$OUT")"
    echo "Ergebnis-Archiv: ${OUT}.tar.gz"
  fi
}
trap restore EXIT
trap 'exit 130' INT TERM

if [ "$WAS_ACTIVE" = "active" ]; then
  echo "Halte ${SERVICE} an."
  systemctl stop "$SERVICE"
fi

"$APP_DIR/.venv/bin/python3" "$SCRIPT_DIR/latenz_messung.py" --dauer "$DAUER" --out "$OUT" ${LATENZ_BOX:+--box "$LATENZ_BOX"}
