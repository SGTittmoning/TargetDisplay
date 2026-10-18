#!/bin/bash
# Speichert die PIN ({"pin": "..."}) von STDIN auf der Boot-Partition.
# Aufruf durch die App per sudo; Begruendung und Ablauf siehe
# targetdisplay-save-lib.sh.

set -euo pipefail

# shellcheck source=targetdisplay-save-lib.sh
. "$(dirname "$(readlink -f "$0")")/targetdisplay-save-lib.sh"

save_boot_json "targetdisplay-pin.json"
exit 0
