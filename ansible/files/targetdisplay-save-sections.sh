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
# schreibt es unveraendert nach $TARGET. Bewusst kein Parsing/Validieren
# hier - main.py validiert vor dem Aufruf, dieses Skript soll so simpel
# wie moeglich bleiben (kleinere Angriffsflaeche fuer das sudoers-Recht).
#
# Schreibt atomar (temp-Datei + mv statt direktem "cat >"): "mv" innerhalb
# derselben Partition ist ein einzelner, unteilbarer Verzeichnis-Eintrag-
# Wechsel - ein Stromausfall waehrend main.py hier hineinschreibt trifft so
# entweder die alte, vollstaendige Datei oder gar keine, nie eine
# angebrochene/kaputte.
#
# WICHTIG: "[ cond ] && cmd" als LETZTE Anweisung einer Funktion ist unter
# "set -e" gefaehrlich (siehe targetdisplay-reboot-guard.sh fuer die
# ausfuehrliche Erklaerung) - deshalb ueberall ein abschliessendes
# "return 0"/"exit 0".

set -euo pipefail

BOOT_DIR="/boot/firmware"
TARGET="$BOOT_DIR/targetdisplay-sections.json"
TMP="$TARGET.tmp"

bootro_now() { raspi-config nonint get_bootro_now; }   # 0=aktiv (ro), 1=inaktiv (rw)

was_ro=0
[ "$(bootro_now)" -eq 0 ] && was_ro=1
[ "$was_ro" -eq 1 ] && mount -o remount,rw "$BOOT_DIR"

cat > "$TMP"
mv "$TMP" "$TARGET"

[ "$was_ro" -eq 1 ] && mount -o remount,ro "$BOOT_DIR"
exit 0
