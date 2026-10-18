#!/bin/bash
# Schreibzugriffe auf die Boot-Partition. Wird per "source" eingebunden
# (Reboot-Guard, Zaehler-Reset, Overlay- und Speicher-Bibliothek).
#
# Die Boot-Partition ist im Normalbetrieb per "bootro" (raspi-config)
# read-only gemountet, unabhaengig vom Root-Overlay. Sie laesst sich live per
# remount umschalten.

BOOT_DIR="/boot/firmware"

bootro_now() { raspi-config nonint get_bootro_now; }   # 0=aktiv (ro), 1=inaktiv (rw)

# with_boot_rw <befehl> [argumente]: fuehrt den Befehl bei beschreibbarer
# Boot-Partition aus. War sie read-only, wird sie davor beschreibbar und
# danach wieder read-only gemountet, auch wenn der Befehl scheitert.
# Rueckgabe: Exit-Code des Befehls; 1 ohne Ausfuehrung, wenn das Umschalten
# scheitert (die Partition bleibt dann read-only).
with_boot_rw() {
  local was_ro=0 rc=0
  if [ "$(bootro_now)" -eq 0 ]; then
    was_ro=1
    if ! mount -o remount,rw "$BOOT_DIR"; then
      mount -o remount,ro "$BOOT_DIR" || true
      return 1
    fi
  fi
  "$@" || rc=$?
  if [ "$was_ro" -eq 1 ]; then
    mount -o remount,ro "$BOOT_DIR"
  fi
  return "$rc"
}
