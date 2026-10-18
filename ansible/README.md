# TargetDisplay — Ansible

Self-contained Ansible setup for installing and maintaining TargetDisplay on a fresh Raspberry Pi (current Raspberry Pi OS **Lite**, 64-bit — no desktop environment needed, this installs its own minimal X11 session). The read-only-filesystem approach and maintenance tooling follow the same pattern used by our other Pi-kiosk projects, adapted for TargetDisplay's X11-based touchscreen app (buttons, touch-pan, timers) rather than a passive headless video kiosk.

Verified end-to-end against a real, freshly-flashed Pi 4 (Raspberry Pi OS Lite, Debian 13/Trixie, 64-bit).

## Setup

```bash
cd ansible
cp inventory.yml.dist inventory.yml   # fill in your host(s) and targetdisplay_stands (name + camera URL per stand)
ansible-playbook -i inventory.yml install.yml --limit <host>

# optional, once you've confirmed the display and touch input both work:
ansible-playbook -i inventory.yml install.yml --limit <host> -e targetdisplay_enable_overlay=true
```

`inventory.yml` holds real credentials (camera URLs with passwords) — keep it out of version control (already gitignored).

To test a feature branch (or a specific tag/commit) before it's merged, without changing the default for anyone else, pass `targetdisplay_git_version` — it only affects which ref `git` checks out, nothing else in the playbook:

```bash
ansible-playbook -i inventory.yml install.yml --limit <host> -e targetdisplay_git_version=feature/mein-branch   # oder ein Tag, z.B. v0.12.3
```

Omit it (or re-run without the flag) to go back to the default (`main`) on the next deploy.

`targetdisplay_stands` is a **fleet-wide** list (name + camera URL per stand) deployed identically to every host in the group (a single host can add entries of its own via `targetdisplay_extra_stands`, e.g. a test stand on a test device) — it doesn't say which stand a *given* device shows. That's chosen once, on the device itself, the first time it boots (see "First-run setup wizard" below); there's no per-host camera/region/PIN variable to fill in here anymore. This makes the same install work for any number of stands, at this club or another, without touching the playbook.

## What `install.yml` does

- Installs X11, `matchbox-window-manager`, touch input tooling, and the Python/OpenCV stack
- Creates a dedicated, unprivileged system user (`targetdisplay_service_user`, default `targetdisplay`) and launches the X11 session via a systemd service (`targetdisplay.service`, `startx` + `.xinitrc`) running as that user, not under the general-purpose admin account. Deliberately **no** `PAMName=login`: it moves the session into its own logind cgroup, which stops `systemctl stop/restart` from ever reaching the real process tree — device access instead relies solely on group membership (`video`/`render`/`input`)
- Configures persistent journald logging (own drop-in overriding Raspberry Pi OS's default volatile-storage drop-in) so logs survive a reboot — needed to debug anything in the failure-escalation path below
- Forces the display resolution to match the production stands (1280×800, `hdmi_mode=27` + `hdmi_ignore_edid`) regardless of which physical monitor is attached, via `targetdisplay_screen_width`/`_height`
- Deploys the TargetDisplay app itself (`git pull` of the default branch, or `targetdisplay_git_version` if set — see above — + a `--system-site-packages` venv owned by the service user, dependencies from the app's `requirements.txt`), then overlays a custom logo if one is present (see "Custom logo" below). `targetdisplay.service` restarts automatically whenever any of these actually changed anything (checkout, venv, `config.yml`, logo, `.xinitrc`, or the unit file itself) — not just on a unit-file change as in earlier versions of this playbook, which used to leave an already-running process serving the old code after a code-only redeploy (e.g. switching `targetdisplay_git_version`) until a manual `systemctl restart`
- Renders `config.yml` from a template using `my_settings_pin` (inventory variable) and `targetdisplay_screen_width`/`_height` (defaults 1280×800 in `vars/common.yml`, shared by `install.yml` and `update-app.yml`), plus the replay settings `targetdisplay_replay_latency_sec` (default 1.0) and `targetdisplay_replay_budget_mb` (default 600) — inventory or `-e`, see the configuration reference in the main README — camera URL, regions and stand name are deliberately **not** templated here, see "First-run setup wizard" below)
- Renders `targetdisplay-stands.json` (the fleet-wide stand list) to the boot partition — see `tasks/stands.yml`
- Installs a narrowly-scoped sudoers rule for the app's own PIN-gated in-app Settings/Restart buttons: the unprivileged service user may run exactly three scripts, one per action (`targetdisplay-save-sections.sh`, `targetdisplay-save-active-stand.sh`, `targetdisplay-save-pin.sh` — each writes just its own override file to the boot partition) plus `/usr/sbin/reboot`, nothing else — see `tasks/settings_sudo.yml`
- Sets up a persistent reboot-guard: if the app can't get a fresh camera frame for `STREAM_STALE_TIMEOUT_SEC` (10s), it exits so systemd restarts it; after `StartLimitBurst` (5) service starts (every start counts, also a manual restart) within `StartLimitIntervalSec` (300s — a failure cycle itself takes ~36s, the interval is set well above the ~180s that 5 cycles need so the escalation doesn't depend on a few seconds of timing jitter), `OnFailure=` triggers a full Pi reboot via a persistent counter on `/boot/firmware`, capped at `MAX_REBOOTS` (5) to avoid an endless reboot loop on a permanent fault (once the cap is exceeded the counter file is written one last time and then left alone). **The service is never disabled:** once the cap is reached there are no further reboots, and `targetdisplay-retry.timer` restarts the service every 10 minutes while it sits in `failed` — the stands run unattended, so a camera that comes back after hours brings the stand back by itself, no power cycle needed. The counter is cleared by `targetdisplay-reboot-count-reset.timer` once the service has run stable (5 minutes after boot, then every 10 minutes) — see `tasks/reboot_guard.yml`
- Applies baseline OS hardening (`tasks/hardening.yml`): a default-deny-inbound `nftables` firewall (only SSH allowed, syntax-checked before being applied), `avahi-daemon` and Bluetooth disabled/masked (neither is used — the fleet is addressed by static IP, and there's no BT peripheral), and SSH key-only login (password authentication is switched off once the admin user has an `authorized_keys`) and SSH detail hardening (`PermitRootLogin no`, `X11Forwarding no`, idle sessions dropped after 5 minutes via `ClientAliveInterval`/`ClientAliveCountMax`). `targetdisplay.service` itself runs sandboxed (`ProtectSystem=strict` with only the service user's home, `/boot/firmware` and `/var/log` writable, private `/tmp`, kernel/clock/cgroup protection), see the unit template
- Configures time synchronisation (`tasks/timesync.yml`): `systemd-timesyncd` against the local `ntpserver`, plus a NetworkManager dispatcher hook that restarts `timesyncd` as soon as the link is up. Without it, the first sync after a boot took about 7.5 minutes (timesyncd tries before the network is up and then backs off exponentially; under NetworkManager it never hears about the network coming up) — and with no RTC the clock is wrong until then. `update-app.yml` re-applies this too, so an already running stand with the overlay active picks it up without a full `install.yml`
- Sets up a boot logo (`tasks/boot_splash.yml`, Plymouth): an image centred on a plain background instead of kernel messages. **On by default**; opt out with `targetdisplay_boot_splash: false` (inventory or `-e`; this only skips setting it up, an already configured boot logo stays — to remove one by hand, switch the Plymouth theme back and drop `quiet splash plymouth.ignore-serial-consoles logo.nologo` from `cmdline.txt`). It adds those tokens to `cmdline.txt` and `disable_splash=1` to `config.txt`, so kernel messages are no longer shown on the screen (use `journalctl -k -b`); the logo appears from the next boot on. The image is `ansible/files/splash.png` on the control machine if present (club specific, not in the repo), otherwise the app's logo (`ressources/logo.png`, i.e. the shipped placeholder or your own `files/logo.png`). Background colour (`targetdisplay_boot_splash_background`, RGB list with values from 0.0 to 1.0, default black `[0, 0, 0]`) and maximum size (`targetdisplay_boot_splash_max_percent`, default 70) are adjustable. The theme lives in the initramfs, so it is rebuilt after every change (the initramfs rebuild remounts the boot partition writable only for that moment; the `cmdline.txt`/`config.txt` edits need it writable already, which is always the case in `install.yml` and in phase 2 of `update-app.yml`); on a stand with the overlay active use `update-app.yml`
- Hides the mouse pointer after a second of inactivity (`unclutter`, started from `.xinitrc`; `targetdisplay_cursor_hide_idle_sec` changes the delay; the `unclutter` package comes with `install.yml` only — `update-app.yml` renders the `.xinitrc` but does not install it)
- Optionally (`targetdisplay_enable_overlay=true`) makes the root filesystem and boot partition read-only, so a hard power cut (this device isn't cleanly shut down — it's switched off at the wall) can't corrupt the SD card/USB drive

## First-run setup wizard

A device that hasn't got a stand selected yet, a calibrated region, or has never moved off the built-in default PIN (`"1234"`) shows an on-device wizard instead of the normal kiosk screen: pick a stand from the list deployed above, drag the four corners into place for each region, then set a real PIN. No PIN is required for this first pass — the device isn't in service yet, there's nothing to protect. Each step is written to the boot partition as soon as it's completed, so the wizard always resumes at exactly the step still missing, even across a power loss mid-step. All three (stand, regions, PIN) can be changed again later from the in-app Settings screen, which is PIN-gated as normal at that point.

## Pre-seeding stand and regions (`boot-overrides.yml`)

Ansible deliberately does not decide which stand a device shows or where its regions are — that is what the wizard asks on the device. If you already know both (e.g. a replacement unit that goes to exactly the same position with the same camera alignment as before), `boot-overrides.yml` writes the two override files `targetdisplay-active-stand.json` and `targetdisplay-sections.json` for you, so the wizard skips stand selection and corner calibration and only asks for the one-time PIN change (and only while the PIN is still the default `1234` — with `my_settings_pin` set to something else there is no PIN step either; this playbook never writes a PIN):

```bash
# values live in ansible/boot-overrides/<stand>/ (gitignored, see ansible/boot-overrides.example/)
ansible-playbook -i inventory.yml boot-overrides.yml --limit <host> -e stand=stand1
```

It writes through the same `sudo` scripts the app itself uses for its Settings menu (JSON check, atomic write, boot partition remounted writable only for that moment), so it also works on a device with the overlay/bootro already active — no rw/ro cycle needed. Files that already have the wanted content are left alone, so running it again is harmless — but a file whose content differs is overwritten, including a stand or region someone picked on the device itself via the Settings menu. The app reads the files only at start, so a running service picks up the new values after `systemctl restart targetdisplay`. The values are validated on the control machine first (stand `id` must be in `targetdisplay_stands`, four `[x, y]` corners per region).

## Touch calibration

`files/99-calibration.conf` ships with placeholder values for a generic eGalax touch controller — every physical touchscreen has slightly different calibration numbers. After a fresh install (before enabling the read-only filesystem!), calibrate the actual screen with `xinput_calibrator` and update the values in `files/99-calibration.conf`. The `copy` task uses `force: no`, so it won't overwrite a file that's already been hand-calibrated on the device.

Hosts with a different touch controller — a different `MatchProduct` name, e.g. a replacement unit with newer hardware or firmware — can use their own file instead. Put it next to the default one and name it per host in the inventory (variable `targetdisplay_touch_calibration_file`, a file name under `files/`; default `99-calibration.conf`):

```yaml
stand5.example.lan:
  targetdisplay_touch_calibration_file: 99-calibration.conf.stand5
```

Check which controller a device really reports with `DISPLAY=:0 xinput list --name-only`. `MatchProduct` in X11 matches as a **substring**, so a generic `...TouchController` entry also matches a `...TouchController Stylus` device — just with the wrong values; such a host needs its own file.

Because the `copy` task keeps `force: no`, the variable only takes effect where `/usr/share/X11/xorg.conf.d/99-calibration.conf` does not exist yet (fresh install). On an already installed host the existing file is not replaced — delete it on the device first (or copy the intended file there by hand), then run `install.yml` again.

## Custom logo

The repo ships a generic placeholder logo (`ressources/logo.png`) that comes along automatically with the git checkout — think of it as the "dist" logo. To show your own club/range logo on every stand instead, drop a `logo.png` (RGBA supported) at `ansible/files/logo.png` **on the Ansible control machine** — deliberately not committed (already gitignored) since it's specific to your club. `install.yml` picks it up automatically on every run from then on and overlays it onto each host, no per-device copying needed. Remove that file again to fall back to the shipped placeholder on the next run.

## Storage hardening (optional)

Since the device is power-cycled without a clean shutdown, making the filesystem read-only protects against corruption, via `raspi-config`'s built-in overlay + bootro mechanism, gated behind `targetdisplay_enable_overlay` instead of being always-on.

With it active, nothing on disk changes at runtime — updates need a small dance to temporarily lift the read-only state, apply them, and lock it back down:

- `maintain.yml` does this automatically via `files/targetdisplay-update.sh`
- `update-app.yml` does the same for app updates via `targetdisplay-writable.sh rw/ro` (see "Updating a running stand")
- `files/targetdisplay-writable.sh` (manual config edits, e.g. touch calibration) and `files/targetdisplay-update.sh` (apt upgrades) are also deployed to `/root/bin/` on the device itself, for when Ansible access isn't available

`overlayroot=tmpfs` on the kernel command line alone does not make an overlay — the overlay mount is done by a script **inside the initramfs** that the firmware loads. If the `overlayroot` package gets installed while the boot partition is already read-only, apt builds a new initrd but cannot copy it to `/boot/firmware`; the firmware keeps loading the old one, `raspi-config nonint get_overlay_now` still says "active" (it only reads the command line), and the root filesystem stays plainly writable. `tasks/overlay_initramfs.yml` (used by `install.yml` when enabling the overlay and by `update-app.yml` before it re-enables it) therefore checks the initramfs files the firmware really loads and regenerates them if the hook is missing; `update-app.yml` finally asserts — when it restores read-only — that `/` is really mounted as `overlay`. To check by hand: `findmnt -n -o FSTYPE /` must print `overlay`; `get_overlay_now` alone proves nothing.

The on-device scripts follow the same rule: `targetdisplay-writable.sh ro` and `targetdisplay-update.sh apply` enable the overlay **while the boot partition is still writable**, check the initramfs for the hook (regenerating it if missing, failing loudly if it is still missing) and only then mount the boot partition read-only.

## Updating a running stand

Two different kinds of update, two different playbooks:

| What | How | Touches git? |
|---|---|---|
| **OS packages** (`apt full-upgrade`, autoremove, clean) | `maintain.yml` (via `targetdisplay-update.sh`, handles the read-only dance and the reboots itself) | no |
| **App code** (a new commit on `main`, or another ref) | `update-app.yml` — checkout (`force: yes`), venv, `config.yml`, logo, time sync, boot logo, restart when anything changed; lifts and restores the read-only state itself, checks the overlayroot hook in the initramfs before locking and verifies afterwards that `/` is really mounted as `overlay`. Pin a ref with `-e targetdisplay_git_version=<branch\|tag\|commit>` | yes |
| **Everything** (packages, hardening, scripts, units, app) | `install.yml` — refuses to run while the overlay is active (see below) | yes |

`maintain.yml` does **not** deploy app code. To roll out a new app version:

```bash
ansible-playbook -i inventory.yml update-app.yml --limit <host>
```

`update-app.yml` handles one host at a time and stops at the first failure. With the overlay active it runs: `targetdisplay-writable.sh rw` (overlay off → reboot → boot partition writable), the app update, `targetdisplay-writable.sh ro` (boot-ro + overlay on → reboot), then verifies overlay, boot-ro and `targetdisplay.service`. On a host without overlay it only updates and restarts the service. Expect two reboots (about 3 minutes) per stand.

A marker file `/boot/firmware/.targetdisplay-update-restore` records "lock read-only again afterwards". If a run aborts (network down, update task failed), the stand stays writable but keeps running normally; run the same command again and it continues and re-locks at the end. `-e update_restore_ro=auto|true|false` overrides the decision (default `auto`: restore when overlay/boot-ro were active at start or the marker exists).

`update-app.yml` also renders `.xinitrc` and `targetdisplay.service` — from the `ansible/` tree on the control machine, not from the freshly pulled app checkout (`targetdisplay_git_version` only selects the app code). It also (re)applies time synchronisation (`tasks/timesync.yml`) and the boot logo (`tasks/boot_splash.yml`: installs `plymouth`, edits `cmdline.txt`/`config.txt`, rebuilds the initramfs; skip with `targetdisplay_boot_splash: false`). It does not touch the base package set (e.g. `unclutter`), hardening, `/root/bin/*` scripts, the reboot-guard/retry/reset units, `targetdisplay_stands` or the touch calibration. It needs `ntpserver` (like `install.yml`).

**Check the release notes before relying on it for an upgrade:** if a release changes scripts under `ansible/files/` or units/templates (e.g. 0.12.0: reboot-guard that never disables the service, `targetdisplay-retry.timer`; 0.12.1: the `unclutter` package that hides the mouse pointer; 0.12.2/0.12.3: `targetdisplay-writable.sh`/`-update.sh` check the overlayroot hook, the reboot-guard stops rewriting its counter after the cap; 0.13.x: `targetdisplay-save-lib.sh` always removes its temp file, the new shared `targetdisplay-overlay-lib.sh` for `-writable.sh`/`-update.sh`; 0.13.3: shared `targetdisplay-boot-lib.sh`, reboot-guard tolerates a damaged counter file, sudoers user from `targetdisplay_service_user`; 0.13.4: sandboxed `targetdisplay.service`, `targetdisplay-update.sh` reports unreadable package sources), run `install.yml` once on that stand — `update-app.yml` does not bring those along. With the overlay active this needs the manual flow:

```bash
# on the stand (ssh), may ask for an intermediate reboot - the script says so; call it again afterwards
sudo /root/bin/targetdisplay-writable.sh rw

# from the control machine
ansible-playbook -i inventory.yml install.yml --limit <host>

# on the stand again: re-enable overlay + read-only boot partition, then reboot
sudo /root/bin/targetdisplay-writable.sh ro
sudo reboot
```

## Known open points

- **Touch calibration values are per physical unit** — the shipped `99-calibration.conf` is a placeholder from the previous Bullseye-based deployment and needs to be redone per stand after a fresh install (see above).
- **No physical touchscreen has been attached to the test Pi yet** — the resolution is forced to match production (1280×800, confirmed via `fbset -s`), but real touch-input calibration is still unverified against the new `startx`/dedicated-user setup.
- **`matchbox-window-manager` (and an occasional pango fontconfig helper) has been observed once not exiting cleanly on `systemctl stop`**, forcing a `SIGKILL` after `TimeoutStopSec` — seen during a long-running session, not reproduced since, no fix applied. Watch for it if stops start hanging again.
- This has been run repeatedly end-to-end against a real, freshly-flashed Pi 4 — package install, app deploy, startup service, and the reboot-guard failure-escalation path (stream outage → app restarts → full reboot, and recovery mid-cycle) are all verified working, including repeated `systemctl restart` cycles completing cleanly in well under a second, and a full read-only-overlay cycle (enable → verify → back to read-write) is verified working too.
