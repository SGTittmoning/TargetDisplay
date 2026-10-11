#!/bin/bash
# Wird periodisch von targetdisplay-retry.timer aufgerufen. Startet
# targetdisplay.service neu, wenn es auf "failed" steht - das ist der Zustand
# nach erreichtem StartLimit, insbesondere nachdem targetdisplay-reboot-guard.sh
# die Reboot-Obergrenze erreicht hat und nicht mehr rebootet.
#
# Hintergrund: Die Staende laufen unbeaufsichtigt, ein dauerhaft
# abgeschalteter Dienst waere dort nicht mehr behebbar. Statt aufzugeben
# versucht das System es deshalb in festen Abstaenden erneut; faellt z.B. die
# Kamera fuer Stunden aus, kommt der Stand nach ihrer Rueckkehr von selbst
# wieder, ohne Stromzyklus.
#
# Nur "failed" wird neu gestartet. Ein bewusst gestoppter Dienst (z.B.
# "systemctl stop" waehrend einer Wartung) steht auf "inactive" und bleibt
# unberuehrt.
#
# WICHTIG: reset-failed ist noetig, weil systemd einen Start nach erreichtem
# StartLimit sonst mit "start request repeated too quickly" ablehnt.

set -euo pipefail

SERVICE="targetdisplay.service"

if systemctl is-failed --quiet "$SERVICE"; then
  logger -t targetdisplay-retry "$SERVICE steht auf failed - Neustart-Versuch."
  systemctl reset-failed "$SERVICE"
  systemctl start --no-block "$SERVICE"
fi
exit 0
