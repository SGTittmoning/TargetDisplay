#!/bin/bash
# Wird von main.py (Ersteinrichtungs-Assistent / spaeterer Stand-Wechsel im
# Settings-Menue) per sudo aufgerufen, um die Stand-Auswahl fuer DIESES
# Geraet persistent zu speichern - main.py laeuft als unprivilegierter
# targetdisplay-User und kann /boot/firmware nicht selbst beschreibbar
# machen. Ueber sudoers auf genau dieses eine Skript eingegrenzt (siehe
# tasks/settings_sudo.yml).
#
# Liegt bewusst auf der Boot-Partition, nicht in config.yml auf dem
# Root-FS - siehe targetdisplay-save-sections.sh fuer die ausfuehrliche
# Begruendung (Root-Overlay braeuchte sonst zwei Reboots statt eines
# Remounts).
#
# Nimmt das neue JSON ({"id": "..."}) auf STDIN entgegen. Die Stand-ID gegen
# targetdisplay-stands.json prueft main.py vor dem Aufruf; hier wird nur
# geprueft, dass die Eingabe klein genug und gueltiges JSON ist. Ablauf
# (Groessenlimit, JSON-Pruefung, atomares Schreiben, Remount) steht in
# targetdisplay-save-lib.sh.

set -euo pipefail

# shellcheck source=targetdisplay-save-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-save-lib.sh"

save_boot_json "targetdisplay-active-stand.json"
exit 0
