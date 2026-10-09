# Curatables Raspberry Pi appliance image

Builds a flashable Raspberry Pi OS Lite (64-bit, trixie) image with
Curatables pre-installed and enabled — flash it, boot it, open
`http://curatables.local`, set a parent password, and you're running. No
terminal needed after flashing.

## What's in the image

- Curatables at `/opt/curatables` (root-owned, read-only to the service),
  running under a dedicated unprivileged `curatables` user, on **port 80**.
  The library lives in `/home/curatables/curatables-data`.
- A Python 3.13 venv with all deps (a **fresh yt-dlp** as of build time),
  plus `ffmpeg`, `git` and `deno` (yt-dlp's JS runtime for YouTube).
- systemd units enabled at boot:
  - `curatables.service` — the server.
  - `curatables-updater.path` + `.service` — the privileged helper behind
    the dashboard's **Settings → Updates → Update yt-dlp** button, so the
    baked-in (and inevitably aging) yt-dlp can be updated from the UI.
  - `curatables-backup.timer` — nightly snapshot of the database and
    `config.json` into `/var/lib/curatables-backups` (see
    [`../docs/backup.md`](../docs/backup.md)).
  - `curatables-firstboot.service` — applies the Wi-Fi file below.
- mDNS via Avahi → reachable at `curatables.local` on the LAN.

## Networking (no terminal needed)

- **Wired Ethernet — automatic.** Plug the Pi into your router; it gets an
  IP via DHCP on boot. Nothing to configure.
- **Wi-Fi — edit a text file.** The boot partition (the small drive that
  appears when you put the SD card in any computer) contains
  `curatables-wifi.txt`. Open it on Windows/macOS/Linux, fill in
  `country`, `ssid`, and `psk`, save, and boot the Pi. A first-boot
  service (`curatables-firstboot.service` → `scripts/firstboot.sh`) reads
  it and joins the network. Edit the file and reboot to switch networks
  later. Windows line endings are handled.

**No default passwords.** The OS login user *and* the parent dashboard
password are both set by you, not baked in:

- **OS / SSH login:** set the username, password (or SSH key), and Wi-Fi
  in **Raspberry Pi Imager → ⚙ (advanced options)** before flashing. The
  image ships with no login password on purpose.
- **Parent dashboard:** the app ships with no password; the first visit
  to `curatables.local` redirects to a one-time setup page where you
  choose it.

## After flashing

- **Media on a USB drive.** An SD card is small and wears out; move the
  library to a USB disk with `scripts/setup-external-drive.sh`. See
  [Media on an external drive](../docs/deployment.md#media-on-an-external-drive-raspberry-pi--usb-disk).
- **Upgrading Curatables.** The image has no `.git`, so clone the repo on
  the Pi and run the installer, which takes a backup first:

  ```bash
  git clone https://github.com/MadeByTokens/curatables.git ~/curatables
  cd ~/curatables && sudo scripts/install.sh --systemd
  ```

  Later upgrades are `git pull` + the same command. See
  [`../docs/upgrade.md`](../docs/upgrade.md).

## Building

Requires, on an x86_64 build host:

- Docker (daemon reachable by your user) and `rsync`.
- **ARM emulation:** the `qemu-aarch64` binfmt handler registered **and** a
  static qemu binary installed. `build.sh` stops if either is missing.

```bash
# one-time (Debian/Ubuntu): static qemu + binfmt registration
sudo apt install qemu-user-static binfmt-support
#   (binfmt only: docker run --privileged --rm tonistiigi/binfmt --install arm64
#    — you still need qemu-user-static for the binary)

./pi-gen/build.sh
```

The build runs entirely in Docker (pi-gen's `build-docker.sh`), takes
~30–60 min, and needs several GB of free disk. The result lands at:

```
pi-gen/.pi-gen/deploy/image_<YYYY-MM-DD>-curatables.img.xz
```

Flash that with Raspberry Pi Imager (use "Use custom" → select the
`.img.xz`), set your login/Wi-Fi in the advanced options, and boot the Pi.

The image is built from the **current checkout** (an rsync of this repo,
minus `.git`/`.venv`/data), so it works on unmerged branches without
pushing anything.

`build.sh` honours `PIGEN_DIR` (pi-gen checkout, default
`pi-gen/.pi-gen`), `PIGEN_REF` (pi-gen branch, default `arm64`) and
`CONTINUE=1` (resume an existing `pigen_work` container instead of
removing it).

In CI, [`.github/workflows/build-image.yml`](../.github/workflows/build-image.yml)
runs the same script (manually, or on a published GitHub Release, which
gets the `.img.xz` attached).

## How it fits together

- `config` — pi-gen settings (image name, Lite/trixie, no baked
  passwords, xz compression).
- `build.sh` — clones pi-gen, stages the source, runs the Docker build.
- `stage-curatables/` — the custom pi-gen stage:
  - `prerun.sh` — starts the stage's rootfs from the previous stage.
  - `00-install-packages/00-packages` — apt deps mirroring
    `scripts/install.sh` (plus `git`).
  - `01-install/00-run.sh` — copies the source into the rootfs.
  - `01-install/01-run-chroot.sh` — service user, venv, deno, systemd
    units (server, updater, backup timer), `run.py --check`, then
    `chown -R root:root /opt/curatables`.
  - `02-firstboot/00-run.sh` — puts `curatables-wifi.txt` on the boot
    partition.
  - `02-firstboot/01-run-chroot.sh` — installs and enables
    `curatables-firstboot.service`.
  - `EXPORT_IMAGE` — marks the stage for image export.

## Build gotchas

- **64-bit comes from pi-gen's `arm64` branch**, not `ARCH=arm64` in
  `config`: pi-gen's `build.sh` hard-sets `ARCH` after sourcing the config,
  so the config value is ignored. The default branch builds 32-bit armhf.
- **Track the branch's release (trixie).** Forcing `RELEASE=bookworm` on
  the arm64 branch debootstraps a base whose apt keyring fails GPG
  verification (`NO_PUBKEY ...`) in `stage0/00-configure-apt`.
- **qemu name check.** pi-gen's `build-docker.sh` gates on
  `which qemu-aarch64`; Ubuntu's `qemu-user-static` ships
  `qemu-aarch64-static`. `build.sh` symlinks a `qemu-aarch64` shim into
  `pi-gen/.binshim/` on `PATH` (actual ARM execution goes through the
  kernel binfmt handler). Without a static qemu binary there is nothing
  to shim and the build stops; CI installs `qemu-user-static` for this.
- **Package lists belong in a sub-stage directory**
  (`00-install-packages/00-packages`). pi-gen only descends into
  sub-stage directories; a `00-packages` at the stage root is silently
  ignored. The stage root holds only `prerun.sh`, `EXPORT_IMAGE` and
  `SKIP_IMAGES`.
- `build-docker.sh` **sources** `config`, so quoted values such as
  `STAGE_LIST="stage0 stage1 ..."` are safe.
- **Boot partition files** go in `${ROOTFS_DIR}/boot/firmware/`; pi-gen
  copies that onto the FAT partition at export.
- **No compiler needed.** All Python deps (curl_cffi, reportlab, ...) have
  aarch64 wheels for Python 3.13, so the image needs no `build-essential`
  or `libffi-dev`. To check a dependency change without a full build, try
  the install in an `arm64v8/debian:trixie` container
  (`docker run --platform linux/arm64 ...`).
- **Deno** installs in the chroot with just `unzip` added; use
  `DENO_INSTALL=/usr/local` so the binary lands at `/usr/local/bin/deno`.
- **Stale container.** `build-docker.sh` refuses to start if a
  `pigen_work` container is left over from an interrupted run; `build.sh`
  removes it unless `CONTINUE=1`.
- **Wi-Fi first boot must run after NetworkManager.** `raspi-config nonint
  do_wifi_ssid_passphrase` / `do_wifi_country` drive `nmcli` and fail with
  "No supported network connection manager found" unless NetworkManager is
  active, so `curatables-firstboot.service` has
  `After=NetworkManager.service` (not `Before=network-pre.target`).
  `nmcli device wifi connect` reactivates the saved profile, so re-running
  on every boot is safe.
