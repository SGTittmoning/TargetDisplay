#!/bin/bash
# Wird von main.py (PIN-Aenderung im Settings-Menue, freiwillig oder im
# Ersteinrichtungs-Assistenten erzwungen solange der PIN noch auf dem
# Standardwert "1234" steht) per sudo aufgerufen, um den geaenderten PIN
# fuer DIESES Geraet persistent zu speichern. Bewusst ein EIGENES Skript
# statt targetdisplay-save-active-stand.sh/-save-sections.sh mitzunutzen -
# jede Funktion bekommt ihr eigenes, engst moegliches Sudo-Recht (siehe
# tasks/settings_sudo.yml), ein kompromittierter main.py-Prozess kann so
# nie mehr als die eine Aktion ausloesen, fuer die gerade ein Aufruf
# tatsaechlich noetig ist.
#
# Nimmt das neue JSON ({"pin": "..."}) auf STDIN entgegen. Format und Laenge
# des PIN validiert main.py vor dem Aufruf; hier wird nur geprueft, dass die
# Eingabe klein genug und gueltiges JSON ist. Ablauf (Groessenlimit,
# JSON-Pruefung, atomares Schreiben, Remount) steht in
# targetdisplay-save-lib.sh.

set -euo pipefail

# shellcheck source=targetdisplay-save-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-save-lib.sh"

save_boot_json "targetdisplay-pin.json"
exit 0
