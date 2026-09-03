# TargetDisplay — Ansible

Self-contained Ansible setup for installing and maintaining TargetDisplay on a fresh Raspberry Pi (current Raspberry Pi OS). The read-only-filesystem approach and maintenance tooling follow the same pattern used by our other Pi-kiosk projects, adapted for TargetDisplay's X11-based touchscreen app (buttons, touch-pan, timers) rather than a passive headless video kiosk.

Verified end-to-end against a real, freshly-flashed Pi 4 (Debian 13/Trixie, 64-bit).

## Setup

```bash
cd ansible
cp inventory.yml.dist inventory.yml   # fill in your host(s) and targetdisplay_stands (name + camera URL per stand)
ansible-playbook -i inventory.yml install.yml --limit <host>

# optional, once you've confirmed the display and touch input both work:
ansible-playbook -i inventory.yml install.yml --limit <host> -e targetdisplay_enable_overlay=true
```

`inventory.yml` holds real credentials (camera URLs with passwords) — keep it out of version control (already gitignored).

`targetdisplay_stands` is a **fleet-wide** list (name + camera URL per stand) deployed identically to every host in the group — it doesn't say which stand a *given* device shows. That's chosen once, on the device itself, the first time it boots (see "First-run setup wizard" below); there's no per-host camera/region/PIN variable to fill in here anymore. This makes the same install work for any number of stands, at this club or another, without touching the playbook.

## What `install.yml` does

- Installs X11, `matchbox-window-manager`, touch input tooling, and the Python/OpenCV stack
- Creates a dedicated, unprivileged system user (`targetdisplay_service_user`, default `targetdisplay`) and launches the X11 session via a systemd service (`targetdisplay.service`, `startx` + `.xinitrc`) running as that user — no `nodm`/autologin under the general-purpose admin account. Deliberately **no** `PAMName=login`: it moves the session into its own logind cgroup, which stops `systemctl stop/restart` from ever reaching the real process tree — device access instead relies solely on group membership (`video`/`render`/`input`)
- Configures persistent journald logging (own drop-in overriding Raspberry Pi OS's default volatile-storage drop-in) so logs survive a reboot — needed to debug anything in the failure-escalation path below
- Forces the display resolution to match the production stands (1280×800, `hdmi_mode=27` + `hdmi_ignore_edid`) regardless of which physical monitor is attached, via `targetdisplay_screen_width`/`_height`
- Deploys the TargetDisplay app itself (`git pull` + a `--system-site-packages` venv owned by the service user, dependencies from the app's `requirements.txt`)
- Renders `config.yml` from a template using the per-host inventory variables (`targetdisplay_screen_width`/`_height`, `my_settings_pin` — camera URL, regions and stand name are deliberately **not** templated here, see "First-run setup wizard" below)
- Renders `targetdisplay-stands.json` (the fleet-wide stand list) to the boot partition — see `tasks/stands.yml`
- Installs a narrowly-scoped sudoers rule for the app's own PIN-gated in-app Settings/Restart buttons: the unprivileged service user may run exactly three scripts, one per action (`targetdisplay-save-sections.sh`, `targetdisplay-save-active-stand.sh`, `targetdisplay-save-pin.sh` — each writes just its own override file to the boot partition) plus `/usr/sbin/reboot`, nothing else — see `tasks/settings_sudo.yml`
- Sets up a persistent reboot-guard: if the app can't get a fresh camera frame for `STREAM_STALE_TIMEOUT_SEC` (10s), it exits so systemd restarts it; after `StartLimitBurst` (5) failed restarts within `StartLimitIntervalSec` (180s), `OnFailure=` triggers a full Pi reboot via a persistent counter on `/boot/firmware`, capped at `MAX_REBOOTS` (5) to avoid an endless reboot loop on a permanent fault — see `tasks/reboot_guard.yml`
- Optionally (`targetdisplay_enable_overlay=true`) makes the root filesystem and boot partition read-only, so a hard power cut (this device isn't cleanly shut down — it's switched off at the wall) can't corrupt the SD card/USB drive

## First-run setup wizard

A device that hasn't got a stand selected yet, a calibrated region, or has never moved off the built-in default PIN (`"1234"`) shows an on-device wizard instead of the normal kiosk screen: pick a stand from the list deployed above, drag the four corners into place for each region, then set a real PIN. No PIN is required for this first pass — the device isn't in service yet, there's nothing to protect. Each step is written to the boot partition as soon as it's completed, so the wizard always resumes at exactly the step still missing, even across a power loss mid-step. All three (stand, regions, PIN) can be changed again later from the in-app Settings screen, which is PIN-gated as normal at that point.

## Touch calibration

`files/99-calibration.conf` ships with placeholder values for a generic eGalax touch controller — every physical touchscreen has slightly different calibration numbers. After a fresh install (before enabling the read-only filesystem!), calibrate the actual screen with `xinput_calibrator` and update the values in `files/99-calibration.conf` for that stand, or drop a per-host override next to it. The `copy` task uses `force: no`, so it won't overwrite a file that's already been hand-calibrated on the device.

## Storage hardening (optional)

Since the device is power-cycled without a clean shutdown, making the filesystem read-only protects against corruption, via `raspi-config`'s built-in overlay + bootro mechanism, gated behind `targetdisplay_enable_overlay` instead of being always-on.

With it active, nothing on disk changes at runtime — updates need a small dance to temporarily lift the read-only state, apply them, and lock it back down:

- `maintain.yml` does this automatically via `files/targetdisplay-update.sh`
- `files/targetdisplay-writable.sh` (manual config edits, e.g. touch calibration) and `files/targetdisplay-update.sh` (apt upgrades) are also deployed to `/root/bin/` on the device itself, for when Ansible access isn't available

## Known open points

- **Touch calibration values are per physical unit** — the shipped `99-calibration.conf` is a placeholder from the previous Bullseye-based deployment and needs to be redone per stand after a fresh install (see above).
- **No physical touchscreen has been attached to the test Pi yet** — the resolution is forced to match production (1280×800, confirmed via `fbset -s`), but real touch-input calibration is still unverified against the new `startx`/dedicated-user setup.
- **`matchbox-window-manager` (and an occasional pango fontconfig helper) has been observed once not exiting cleanly on `systemctl stop`**, forcing a `SIGKILL` after `TimeoutStopSec` — seen during a long-running session, not reproduced since, no fix applied. Watch for it if stops start hanging again.
- This has been run repeatedly end-to-end against a real, freshly-flashed Pi 4 — package install, app deploy, startup service, and the reboot-guard failure-escalation path (stream outage → app restarts → full reboot, and recovery mid-cycle) are all verified working, including repeated `systemctl restart` cycles completing cleanly in well under a second, and a full read-only-overlay cycle (enable → verify → back to read-write) is verified working too.
