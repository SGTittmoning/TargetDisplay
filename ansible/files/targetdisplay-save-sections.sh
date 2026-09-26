#!/bin/bash
# Wird von main.py (Settings-Screen) per sudo aufgerufen, um section_full/
# section_detail-Aenderungen persistent zu speichern - main.py laeuft als
# unprivilegierter targetdisplay-User und kann /boot/firmware nicht selbst
# beschreibbar machen. Ueber sudoers auf genau dieses eine Skript
# eingegrenzt (siehe tasks/settings_sudo.yml).
#
# Liegt bewusst auf der Boot-Partition, nicht in config.yml auf dem Root-FS:
# /boot/firmware kann LIVE per remount umgeschaltet werden, das Root-Overlay
# dagegen nur per Reboot (raspi-config disable/enable_overlayfs) - eine
# Aenderung an config.yml wuerde bei aktivem Overlay also zwei Reboots
# brauchen, um dauerhaft zu wirken. Diese Datei hier ist deshalb eine
# Override-Ebene: main.py liest beim Start zuerst config.yml (Baseline),
# dann - falls vorhanden - diese Datei, und nutzt deren Werte falls
# gesetzt. So wirkt eine Aenderung sofort UND uebersteht einen Reboot,
# auch mit aktivem Overlay, ganz ohne dessen Reboot-Tanz.
#
# Nimmt das neue JSON auf STDIN entgegen (main.py schreibt es dorthin) und
# legt es unveraendert auf der Boot-Partition ab. Inhaltlich (Format, Werte)
# validiert main.py vor dem Aufruf; hier wird nur geprueft, dass die Eingabe
# klein genug und gueltiges JSON ist. Ablauf (Groessenlimit, JSON-Pruefung,
# atomares Schreiben, Remount) steht in targetdisplay-save-lib.sh.

set -euo pipefail

# shellcheck source=targetdisplay-save-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-save-lib.sh"

save_boot_json "targetdisplay-sections.json"
exit 0
