#!/bin/bash
# Regressionstests fuer die Skripte unter ansible/files/.
#
# Braucht weder root noch Hardware: raspi-config, mount, systemctl, reboot und
# logger werden durch Stubs ersetzt, die ihre Aufrufe protokollieren; der
# Pfad /boot/firmware wird in Testkopien der Skripte auf ein Temp-Verzeichnis
# umgebogen. Abhaengigkeiten: bash, coreutils, sed, awk, python3.
#
# Aufruf:  tests/run-script-tests.sh

set -u

ROOT=$(cd "$(dirname "$0")/.." && pwd)
FILES="$ROOT/ansible/files"
WORK=$(mktemp -d)
trap 'chmod -R u+w "$WORK" 2>/dev/null; rm -rf "$WORK"' EXIT

pass=0; fail=0
ok()   { pass=$((pass + 1)); printf '  ok    %s\n' "$1"; }
bad()  { fail=$((fail + 1)); printf '  FEHLER %s\n         %s\n' "$1" "${2:-}"; }
check() { if [ "$2" = "$3" ]; then ok "$1"; else bad "$1" "erwartet '$2', erhalten '$3'"; fi; }

# ---------------------------------------------------------------- Stubs
STUBS="$WORK/stubs"; mkdir -p "$STUBS"
for c in logger reboot; do
  printf '#!/bin/bash\necho "%s $*" >> "$STUB_LOG"\n' "$c" > "$STUBS/$c"
done
cat > "$STUBS/raspi-config" <<'STUB'
#!/bin/bash
echo "raspi-config $2" >> "$STUB_LOG"
case "$2" in
  get_bootro_now) echo "${BOOTRO:-1}" ;;
  get_overlay_now) echo "${OVERLAY:-1}" ;;
esac
exit 0
STUB
cat > "$STUBS/id" <<'STUB'
#!/bin/bash
echo 0
STUB
cat > "$STUBS/lsinitramfs" <<'STUB'
#!/bin/bash
# Hook vorhanden, ausser STUB_HOOK=0; nach update-initramfs vorhanden,
# ausser STUB_HOOK_STAYS_MISSING=1.
if [ "${STUB_HOOK:-1}" = 1 ] || { [ -e "$STUB_LOG.rebuilt" ] && [ "${STUB_HOOK_STAYS_MISSING:-0}" != 1 ]; }; then
  echo "scripts/local-bottom/overlayroot"
else
  echo "scripts/local-bottom/other"
fi
STUB
cat > "$STUBS/update-initramfs" <<'STUB'
#!/bin/bash
echo "update-initramfs $*" >> "$STUB_LOG"
touch "$STUB_LOG.rebuilt"
STUB
cat > "$STUBS/apt-get" <<'STUB'
#!/bin/bash
echo "apt-get $*" >> "$STUB_LOG"
[ "$1" = update ] && [ "${STUB_APT_UPDATE_FAIL:-0}" = 1 ] && exit 100
[ "$1" = full-upgrade ] && [ "${STUB_APT_UPGRADE_FAIL:-0}" = 1 ] && exit 100
exit 0
STUB
cat > "$STUBS/mount" <<'STUB'
#!/bin/bash
echo "mount $*" >> "$STUB_LOG"
case "$*" in
  *remount,rw*) [ "${FAIL_MOUNT_RW:-0}" = 1 ] && exit 32 ;;
esac
exit 0
STUB
cat > "$STUBS/mv" <<'STUB'
#!/bin/bash
[ "${FAIL_MV:-0}" = 1 ] && exit 1
exec /bin/mv "$@"
STUB
cat > "$STUBS/systemctl" <<'STUB'
#!/bin/bash
echo "systemctl $*" >> "$STUB_LOG"
case "$1" in
  is-active) exit "${STUB_INACTIVE:-0}" ;;
  is-failed) exit "${STUB_FAILED:-1}" ;;   # Exit 0 = Dienst steht auf failed
  show)
    case "$*" in
      *NRestarts*) echo "${STUB_NRESTARTS:-0}" ;;
      *ActiveEnterTimestampMonotonic*) echo "${STUB_ACTIVE_ENTER_US:-1000000}" ;;
    esac ;;
esac
exit 0
STUB
chmod +x "$STUBS"/*

# Testkopien der Skripte mit umgebogenem Boot-Pfad
BIN="$WORK/bin"; BOOT="$WORK/boot"; mkdir -p "$BIN" "$BOOT"
for f in "$FILES"/*.sh; do
  sed -e "s#/boot/firmware#$BOOT#g" -e "s#/proc/uptime#$WORK/uptime#g" "$f" > "$BIN/$(basename "$f")"
  chmod +x "$BIN/$(basename "$f")"
done

export STUB_LOG="$WORK/stub.log"
export PATH="$STUBS:$PATH"
reset() { : > "$STUB_LOG"; rm -rf "$BOOT"; mkdir -p "$BOOT"; unset STUB_APT_UPDATE_FAIL STUB_APT_UPGRADE_FAIL BOOTRO OVERLAY FAIL_MOUNT_RW FAIL_MV STUB_INACTIVE STUB_FAILED STUB_NRESTARTS STUB_ACTIVE_ENTER_US STUB_HOOK STUB_HOOK_STAYS_MISSING; rm -f "$STUB_LOG.rebuilt"; }
calls() { grep -c "$1" "$STUB_LOG" 2>/dev/null || true; }
lineof() { grep -n -m1 "$1" "$STUB_LOG" 2>/dev/null | cut -d: -f1; }

# =========================================================== boot-lib.sh
echo "boot-lib.sh (with_boot_rw)"
LIB="$BIN/targetdisplay-boot-lib.sh"

reset; BOOTRO=0 bash -c ". '$LIB'; with_boot_rw touch '$BOOT/x'"; rc=$?
check "ro: Befehl ausgefuehrt" 0 "$rc"
check "ro: Datei angelegt" 1 "$([ -f "$BOOT/x" ] && echo 1 || echo 0)"
check "ro: erst rw, dann wieder ro" "rw ro" "$(grep -o 'remount,r[ow]' "$STUB_LOG" | sed 's/remount,//' | xargs)"

reset; BOOTRO=0 bash -c ". '$LIB'; with_boot_rw false"; rc=$?
check "ro, Befehl scheitert: Exit-Code weitergereicht" 1 "$rc"
check "ro, Befehl scheitert: wieder ro" 1 "$(calls 'remount,ro')"

reset; bash -c ". '$LIB'; with_boot_rw true"
check "rw: kein remount" 0 "$(calls 'remount')"

reset; BOOTRO=0 FAIL_MOUNT_RW=1 bash -c ". '$LIB'; with_boot_rw touch '$BOOT/x'" 2>/dev/null; rc=$?
check "remount rw scheitert: Exit 1" 1 "$rc"
check "remount rw scheitert: Befehl nicht ausgefuehrt" 0 "$([ -f "$BOOT/x" ] && echo 1 || echo 0)"

# =========================================================== save-*.sh
echo "save-*.sh"

reset
printf '{"pin": "4711"}' | "$BIN/targetdisplay-save-pin.sh"; rc=$?
check "pin: Exit 0" 0 "$rc"
check "pin: Datei geschrieben" '{"pin": "4711"}' "$(cat "$BOOT/targetdisplay-pin.json")"
check "pin: keine temp-Datei uebrig" "" "$(find "$BOOT" -maxdepth 1 -name '*.tmp')"
check "pin: Boot-Partition rw -> kein remount" 0 "$(calls mount)"

reset
echo '{"id": "stand3"}' | "$BIN/targetdisplay-save-active-stand.sh"
check "active-stand: schreibt eigene Datei" '{"id": "stand3"}' "$(cat "$BOOT/targetdisplay-active-stand.json")"
reset
echo '{"section_full": [[0,0],[1,0],[1,1],[0,1]]}' | "$BIN/targetdisplay-save-sections.sh"
check "sections: schreibt eigene Datei" 1 "$([ -f "$BOOT/targetdisplay-sections.json" ] && echo 1 || echo 0)"

reset; BOOTRO=0 bash -c "echo '{\"a\": 1}' | $BIN/targetdisplay-save-pin.sh"
check "ro-Boot: remount rw" 1 "$(calls 'remount,rw')"
check "ro-Boot: remount ro" 1 "$(calls 'remount,ro')"
check "ro-Boot: rw vor ro" "rw ro" "$(grep -o 'remount,[a-z]*' "$STUB_LOG" | sed 's/remount,//' | tr '\n' ' ' | sed 's/ $//')"

reset; BOOTRO=0
head -c 70000 /dev/zero | tr '\0' 'a' | sed 's/^/{"x": "/; s/$/"}/' | BOOTRO=0 "$BIN/targetdisplay-save-pin.sh" 2>/dev/null; rc=$?
check "zu grosse Eingabe: Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
check "zu grosse Eingabe: nichts geschrieben" "" "$(ls "$BOOT")"
check "zu grosse Eingabe: Partition nie beschreibbar gemacht" 0 "$(calls mount)"

reset; echo 'das ist kein json' | BOOTRO=0 "$BIN/targetdisplay-save-pin.sh" 2>/dev/null; rc=$?
check "ungueltiges JSON: Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
check "ungueltiges JSON: nichts geschrieben" "" "$(ls "$BOOT")"
check "ungueltiges JSON: Partition nie beschreibbar gemacht" 0 "$(calls mount)"

reset; echo '{"pin": "1111"}' > "$BOOT/targetdisplay-pin.json"; chmod 555 "$BOOT"
echo '{"pin": "2222"}' | BOOTRO=0 "$BIN/targetdisplay-save-pin.sh" 2>/dev/null; rc=$?
chmod 755 "$BOOT"
if [ "$(id -u)" -eq 0 ]; then
  echo "  skip  Schreibfehler-Test (als root laesst sich das Schreiben nicht verhindern)"
else
  check "Schreibfehler: Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
  check "Schreibfehler: Partition wieder read-only" 1 "$(calls 'remount,ro')"
  check "Schreibfehler: alte Datei unveraendert" '{"pin": "1111"}' "$(cat "$BOOT/targetdisplay-pin.json")"
fi

reset; echo '{"pin": "1"}' | BOOTRO=0 FAIL_MOUNT_RW=1 "$BIN/targetdisplay-save-pin.sh" 2>/dev/null; rc=$?
check "remount rw scheitert: Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
check "remount rw scheitert: Partition wird read-only gehalten" 1 "$(calls 'remount,ro')"

reset; echo '{"pin": "3333"}' | FAIL_MV=1 "$BIN/targetdisplay-save-pin.sh" 2>/dev/null; rc=$?
check "mv scheitert (Boot rw): Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
check "mv scheitert (Boot rw): keine temp-Datei uebrig" "" "$(find "$BOOT" -maxdepth 1 -name '*.tmp')"

# ============================================== reboot-count-reset.sh
echo "reboot-count-reset.sh"
COUNT="$BOOT/.targetdisplay-reboot-count"
# reset() raeumt auch die STUB_*-Variablen ab; die fuer diesen Lauf gesetzten
# Werte werden deshalb vorher gesichert und danach wieder gesetzt.
run_reset() {
  local ro="${BOOTRO:-}" inactive="${STUB_INACTIVE:-}" restarts="${STUB_NRESTARTS:-}" enter="${STUB_ACTIVE_ENTER_US:-}" up="${UPTIME_S:-400}"
  reset
  [ -z "$ro" ] || export BOOTRO="$ro"
  [ -z "$inactive" ] || export STUB_INACTIVE="$inactive"
  [ -z "$restarts" ] || export STUB_NRESTARTS="$restarts"
  [ -z "$enter" ] || export STUB_ACTIVE_ENTER_US="$enter"
  echo 3 > "$COUNT"
  echo "$up.00 1234.00" > "$WORK/uptime"
  "$BIN/targetdisplay-reboot-count-reset.sh"
}

# Uptime 400 s, Dienst seit 100 s (= 100000000 us) aktiv -> 300 s stabil
UPTIME_S=400 STUB_ACTIVE_ENTER_US=100000000 STUB_NRESTARTS=0 run_reset; rc=$?
check "stabil: Exit 0" 0 "$rc"
check "stabil: Zaehler geloescht" "" "$(find "$BOOT" -maxdepth 1 -name '*reboot-count*')"

STUB_ACTIVE_ENTER_US=100000000 STUB_NRESTARTS=2 run_reset
check "2 Neustarts (< 3): Zaehler geloescht" "" "$(find "$BOOT" -maxdepth 1 -name '*reboot-count*')"

STUB_ACTIVE_ENTER_US=100000000 STUB_NRESTARTS=8 run_reset; rc=$?
check "Neustart-Zyklus: Exit 0" 0 "$rc"
check "Neustart-Zyklus: Zaehler bleibt" 3 "$(cat "$COUNT")"
check "Neustart-Zyklus: Grund im Log" 1 "$(calls 'logger.*automatisch neu gestartet')"

STUB_INACTIVE=3 STUB_ACTIVE_ENTER_US=100000000 run_reset; rc=$?
check "Dienst inaktiv: Exit 0" 0 "$rc"
check "Dienst inaktiv: Zaehler bleibt" 3 "$(cat "$COUNT")"

# Dienst erst seit 350 s aktiv bei Uptime 400 s -> nur 50 s stabil
STUB_ACTIVE_ENTER_US=350000000 STUB_NRESTARTS=0 run_reset
check "erst 50 s aktiv: Zaehler bleibt" 3 "$(cat "$COUNT")"

reset; "$BIN/targetdisplay-reboot-count-reset.sh"; rc=$?
check "ohne Zaehlerdatei: Exit 0" 0 "$rc"

BOOTRO=0 STUB_ACTIVE_ENTER_US=100000000 run_reset
check "ro-Boot: remount rw" 1 "$(calls 'remount,rw')"
check "ro-Boot: remount ro" 1 "$(calls 'remount,ro')"

# ====================================================== reboot-guard.sh
echo "reboot-guard.sh"
COUNT="$BOOT/.targetdisplay-reboot-count"

reset; "$BIN/targetdisplay-reboot-guard.sh"; rc=$?
check "erster Ausfall: Exit 0" 0 "$rc"
check "erster Ausfall: Zaehler 1" 1 "$(cat "$COUNT")"
check "erster Ausfall: Reboot ausgeloest" 1 "$(calls '^reboot')"

reset; echo 4 > "$COUNT"; "$BIN/targetdisplay-reboot-guard.sh"
check "Zaehler 4 -> 5 (= Grenze): Reboot ausgeloest" 1 "$(calls '^reboot')"
check "Zaehler 4 -> 5: Zaehler steht auf 5" 5 "$(cat "$COUNT")"

reset; echo 5 > "$COUNT"; "$BIN/targetdisplay-reboot-guard.sh"; rc=$?
check "Grenze ueberschritten: Exit 0" 0 "$rc"
check "Grenze ueberschritten: kein weiterer Reboot" 0 "$(calls '^reboot')"
check "Grenze ueberschritten: Zaehler steht auf 6" 6 "$(cat "$COUNT")"
check "Grenze ueberschritten: Dienst wird NICHT deaktiviert" 0 "$(calls 'systemctl.*disable')"
check "Grenze ueberschritten: Dienst wird nicht gestoppt" 0 "$(calls 'systemctl.*stop')"
check "Grenze ueberschritten: Grund im Log" 1 "$(calls 'logger.*Grenze von 5')"

reset; echo 5 > "$COUNT"; BOOTRO=0 "$BIN/targetdisplay-reboot-guard.sh"
check "ro-Boot: Zaehler wird geschrieben" 6 "$(cat "$COUNT")"
check "ro-Boot: remount rw" 1 "$(calls 'remount,rw')"
check "ro-Boot: remount ro" 1 "$(calls 'remount,ro')"

reset; echo 6 > "$COUNT"; BOOTRO=0 "$BIN/targetdisplay-reboot-guard.sh"; rc=$?
check "bereits ueber Grenze: Exit 0" 0 "$rc"
check "bereits ueber Grenze: Zaehler bleibt 6" 6 "$(cat "$COUNT")"
check "bereits ueber Grenze: kein remount (kein Schreibzugriff)" 0 "$(calls 'remount')"
check "bereits ueber Grenze: kein Reboot" 0 "$(calls '^reboot')"
check "bereits ueber Grenze: Grund weiter im Log" 1 "$(calls 'logger.*Grenze von 5')"

reset; printf 'x\x00' > "$COUNT"; "$BIN/targetdisplay-reboot-guard.sh"; rc=$?
check "beschaedigter Zaehler: Exit 0" 0 "$rc"
check "beschaedigter Zaehler: zaehlt ab 0, steht auf 1" 1 "$(cat "$COUNT")"
check "beschaedigter Zaehler: Reboot ausgeloest" 1 "$(calls '^reboot')"
check "beschaedigter Zaehler: Hinweis im Log" 1 "$(calls 'logger.*unlesbar')"

reset; : > "$COUNT"; "$BIN/targetdisplay-reboot-guard.sh"
check "leerer Zaehler: Reboot ausgeloest" 1 "$(calls '^reboot')"

# ===================================================== retry.sh
echo "retry.sh"
reset; STUB_FAILED=0 "$BIN/targetdisplay-retry.sh"; rc=$?
check "Dienst failed: Exit 0" 0 "$rc"
check "Dienst failed: reset-failed" 1 "$(calls 'systemctl reset-failed targetdisplay.service')"
check "Dienst failed: start" 1 "$(calls 'systemctl start --no-block targetdisplay.service')"
check "Dienst failed: Grund im Log" 1 "$(calls 'logger.*steht auf failed')"

reset; STUB_FAILED=1 "$BIN/targetdisplay-retry.sh"; rc=$?
check "Dienst nicht failed: Exit 0" 0 "$rc"
check "Dienst nicht failed: kein reset-failed" 0 "$(calls 'reset-failed')"
check "Dienst nicht failed: kein start" 0 "$(calls 'systemctl start')"

# ===================================================== nm-timesync.sh
echo "nm-timesync.sh (NetworkManager-Dispatcher)"
reset; "$BIN/targetdisplay-nm-timesync.sh" eth0 up; rc=$?
check "eth0 up: Exit 0" 0 "$rc"
check "eth0 up: timesyncd wird neu gestartet" 1 "$(calls 'systemctl try-restart systemd-timesyncd.service')"

reset; "$BIN/targetdisplay-nm-timesync.sh" eth0 down
check "eth0 down: nichts" 0 "$(calls 'systemctl')"

reset; "$BIN/targetdisplay-nm-timesync.sh" lo up
check "lo up: nichts" 0 "$(calls 'systemctl')"

reset; "$BIN/targetdisplay-nm-timesync.sh" eth0 dhcp4-change
check "dhcp4-change: nichts" 0 "$(calls 'systemctl')"


# ============================================ writable.sh / update.sh (Status, Ablauf)
echo "writable.sh / update.sh: Status und Zwischen-Reboot (gemeinsame Bibliothek)"

reset; out=$(OVERLAY=0 BOOTRO=0 "$BIN/targetdisplay-writable.sh" status 2>&1)
check "writable status: Vorgang idle" 1 "$(echo "$out" | grep -c 'rw-Vorgang-Status:            idle')"
check "writable status: Overlay laut Kommandozeile" 1 "$(echo "$out" | grep -c 'OverlayFS laut Kommandozeile: ja')"

reset; OVERLAY=0 BOOTRO=0 "$BIN/targetdisplay-writable.sh" rw >/dev/null 2>&1; rc=$?
check "writable rw bei aktivem Overlay: Exit 0" 0 "$rc"
check "writable rw: Overlay abgeschaltet" 1 "$(calls 'disable_overlayfs')"
check "writable rw: Status awaiting-reboot" "awaiting-reboot" "$(cat "$BOOT/.targetdisplay-writable.state" 2>/dev/null)"
out=$(OVERLAY=0 BOOTRO=0 "$BIN/targetdisplay-writable.sh" status 2>&1)
check "writable status zeigt den Vorgang" 1 "$(echo "$out" | grep -c 'rw-Vorgang-Status:            awaiting-reboot')"

: > "$STUB_LOG"; OVERLAY=1 BOOTRO=0 "$BIN/targetdisplay-writable.sh" rw >/dev/null 2>&1; rc=$?
check "writable rw nach Reboot: Exit 0" 0 "$rc"
check "writable rw nach Reboot: Boot-RO aufgehoben" 1 "$(calls 'disable_bootro')"
check "writable rw nach Reboot: Status geloescht" 0 "$([ -f "$BOOT/.targetdisplay-writable.state" ] && echo 1 || echo 0)"

reset; OVERLAY=0 BOOTRO=0 "$BIN/targetdisplay-update.sh" begin >/dev/null 2>&1; rc=$?
check "update begin bei aktivem Overlay: Exit 0" 0 "$rc"
check "update begin: Status awaiting-reboot-1" "awaiting-reboot-1" "$(cat "$BOOT/.targetdisplay-update.state" 2>/dev/null)"
out=$(OVERLAY=1 BOOTRO=0 "$BIN/targetdisplay-update.sh" status 2>&1)
check "update status zeigt den Zyklus" 1 "$(echo "$out" | grep -c 'Update-Zyklus-Status:         awaiting-reboot-1')"
OVERLAY=0 BOOTRO=0 "$BIN/targetdisplay-update.sh" begin >/dev/null 2>&1; rc=$?
check "update begin doppelt: Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"

# ============================================ writable.sh ro (initramfs)
echo "writable.sh ro: Reihenfolge Overlay/initramfs/read-only"

reset; touch "$BOOT/initramfs8"; OVERLAY=1 BOOTRO=1 "$BIN/targetdisplay-writable.sh" ro >/dev/null 2>&1; rc=$?
check "ro: Exit 0" 0 "$rc"
check "ro: enable_overlayfs vor dem Read-only-Mount" 1 "$([ "$(lineof 'enable_overlayfs')" -lt "$(lineof 'remount,ro')" ] && echo 1 || echo 0)"
check "ro: Hook vorhanden -> kein update-initramfs" 0 "$(calls 'update-initramfs')"

reset; touch "$BOOT/initramfs8"; STUB_HOOK=0 OVERLAY=1 BOOTRO=1 "$BIN/targetdisplay-writable.sh" ro >/dev/null 2>&1; rc=$?
check "ro, Hook fehlt: Exit 0 (nach Neuerzeugung)" 0 "$rc"
check "ro, Hook fehlt: update-initramfs aufgerufen" 1 "$(calls 'update-initramfs -u -k all')"
check "ro, Hook fehlt: update-initramfs vor read-only" 1 "$([ "$(lineof 'update-initramfs')" -lt "$(lineof 'remount,ro')" ] && echo 1 || echo 0)"

reset; touch "$BOOT/initramfs8"; STUB_HOOK=0 STUB_HOOK_STAYS_MISSING=1 OVERLAY=1 BOOTRO=1 "$BIN/targetdisplay-writable.sh" ro >/dev/null 2>&1; rc=$?
check "ro, Hook bleibt weg: Exit 1" 1 "$rc"
check "ro, Hook bleibt weg: Boot trotzdem wieder read-only" 1 "$(calls 'remount,ro')"

reset; touch "$BOOT/initramfs8"; OVERLAY=1 BOOTRO=0 "$BIN/targetdisplay-writable.sh" ro >/dev/null 2>&1
check "ro, Boot schon read-only: erst rw (fuer initrd-Kopie)" 1 "$([ "$(lineof 'remount,rw')" -lt "$(lineof 'enable_overlayfs')" ] && echo 1 || echo 0)"
check "ro, Boot schon read-only: am Ende wieder ro" 1 "$([ "$(lineof 'enable_overlayfs')" -lt "$(lineof 'remount,ro')" ] && echo 1 || echo 0)"

# ============================================ update.sh apply (initramfs)
echo "update.sh apply: Reihenfolge Overlay/initramfs/read-only"

reset; touch "$BOOT/initramfs8"; echo awaiting-apply-no-reboot > "$BOOT/.targetdisplay-update.state"
OVERLAY=1 BOOTRO=0 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "apply: Exit 0" 0 "$rc"
check "apply: enable_overlayfs vor dem letzten Read-only-Mount" 1 "$([ "$(lineof 'enable_overlayfs')" -lt "$(grep -n 'remount,ro' "$STUB_LOG" | tail -1 | cut -d: -f1)" ] && echo 1 || echo 0)"

reset; touch "$BOOT/initramfs8"; echo awaiting-apply-no-reboot > "$BOOT/.targetdisplay-update.state"
STUB_HOOK=0 STUB_HOOK_STAYS_MISSING=1 OVERLAY=1 BOOTRO=0 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "apply, Hook bleibt weg: Exit 1" 1 "$rc"
check "apply, Hook bleibt weg: Zustandsdatei bleibt" 1 "$([ -f "$BOOT/.targetdisplay-update.state" ] && echo 1 || echo 0)"

reset; touch "$BOOT/initramfs8"; echo awaiting-apply-no-reboot > "$BOOT/.targetdisplay-update.state"
STUB_APT_UPDATE_FAIL=1 OVERLAY=1 BOOTRO=0 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "apply, Paketquelle unlesbar: Exit 3" 3 "$rc"
check "apply, Paketquelle unlesbar: Upgrade laeuft trotzdem" 1 "$(calls 'apt-get full-upgrade')"
check "apply, Paketquelle unlesbar: Overlay wieder aktiviert" 1 "$(calls 'enable_overlayfs')"
check "apply, Paketquelle unlesbar: Boot wieder read-only" 1 "$([ "$(grep -c 'remount,ro' "$STUB_LOG")" -ge 1 ] && echo 1 || echo 0)"
check "apply, Paketquelle unlesbar: Zustand aufgeraeumt" 0 "$([ -f "$BOOT/.targetdisplay-update.state" ] && echo 1 || echo 0)"
check "apply: update mit --error-on=any" 1 "$(calls 'apt-get update --error-on=any')"

# ============================================ update.sh Abbruch und Wiederholung
echo "update.sh: apply nach abgebrochenem Upgrade"
reset; touch "$BOOT/initramfs8"; echo awaiting-apply-no-reboot > "$BOOT/.targetdisplay-update.state"
STUB_APT_UPGRADE_FAIL=1 OVERLAY=1 BOOTRO=0 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "Upgrade scheitert: Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"
check "Upgrade scheitert: Zustand bleibt fuer die Wiederholung" 1 "$([ -f "$BOOT/.targetdisplay-update.state" ] && echo 1 || echo 0)"
# Zweiter Lauf: Boot-RO ist inzwischen per fstab aus (BOOTRO=1), das Ziel
# bleibt trotzdem read-only.
: > "$STUB_LOG"
OVERLAY=1 BOOTRO=1 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "Wiederholung: Exit 0" 0 "$rc"
check "Wiederholung: Boot-RO wieder gesetzt" 1 "$(calls 'enable_bootro')"
check "Wiederholung: Overlay aktiviert" 1 "$(calls 'enable_overlayfs')"
check "Wiederholung: zuletzt live read-only" "remount,ro" "$(grep -o 'remount,r[ow]' "$STUB_LOG" | tail -1)"

# ============================================ update.sh Fehlerpfade
echo "update.sh: Fehlerpfade"
reset; OVERLAY=1 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "apply ohne begin: Exit 1" 1 "$rc"
check "apply ohne begin: kein apt-get" 0 "$(calls 'apt-get')"

reset; echo kaputt > "$BOOT/.targetdisplay-update.state"
OVERLAY=1 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "apply mit unbekanntem Zustand: Exit 1" 1 "$rc"
check "apply mit unbekanntem Zustand: Zustand bleibt" kaputt "$(cat "$BOOT/.targetdisplay-update.state")"

reset; echo awaiting-reboot-1 > "$BOOT/.targetdisplay-update.state"
OVERLAY=0 "$BIN/targetdisplay-update.sh" apply >/dev/null 2>&1; rc=$?
check "apply bei noch aktivem Overlay: Exit 1" 1 "$rc"
check "apply bei noch aktivem Overlay: kein apt-get" 0 "$(calls 'apt-get')"

reset; "$BIN/targetdisplay-update.sh" unbekannt >/dev/null 2>&1; rc=$?
check "unbekannter Befehl: Exit != 0" 1 "$([ "$rc" -ne 0 ] && echo 1 || echo 0)"

echo
echo "Ergebnis: $pass ok, $fail Fehler"
[ "$fail" -eq 0 ]
