# Curatables Raspberry Pi appliance image

Builds a flashable Raspberry Pi OS Lite (64-bit, bookworm) image with
Curatables pre-installed and enabled — flash it, boot it, open
`http://curatables.local`, set a parent password, and you're running. No
terminal needed after flashing.

## What's in the image

- Curatables at `/opt/curatables`, running under a dedicated unprivileged
  `curatables` user, on **port 80**.
- A Python venv with all deps (a **fresh yt-dlp** as of build time),
  plus `ffmpeg` and `deno` (yt-dlp's JS runtime for YouTube).
- systemd units enabled at boot:
  - `curatables.service` — the server.
  - `curatables-updater.path` + `.service` — the privileged helper behind
    the dashboard's **Settings → Updates → Update yt-dlp** button, so the
    baked-in (and inevitably aging) yt-dlp can be updated from the UI.
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

## Building

Requires Docker and ARM emulation on the build host (any x86_64 box works):

```bash
# one-time: register qemu ARM emulation if not already present
docker run --privileged --rm tonistiigi/binfmt --install arm64
#   (or: sudo apt install qemu-user-static binfmt-support)

./pi-gen/build.sh
```

The build runs entirely in Docker (pi-gen's `build-docker.sh`), takes
~30–60 min, and needs several GB of free disk. The result lands at:

```
pi-gen/.pi-gen/deploy/curatables-<date>-arm64.img.xz
```

Flash that with Raspberry Pi Imager (use "Use custom" → select the
`.img.xz`), set your login/Wi-Fi in the advanced options, and boot the Pi.

The image is built from the **current checkout** (an rsync of this repo,
minus `.git`/`.venv`/data), so it works on unmerged branches without
pushing anything.

## How it fits together

- `config` — pi-gen settings (image name, Lite/arm64/bookworm, no baked
  passwords, xz compression).
- `build.sh` — clones pi-gen, stages the source, runs the Docker build.
- `stage-curatables/` — the custom pi-gen stage:
  - `00-packages` — apt deps mirroring `scripts/install.sh`.
  - `01-install/00-run.sh` — copies the source into the rootfs.
  - `01-install/01-run-chroot.sh` — venv, deno, systemd units, `--check`.
  - `EXPORT_IMAGE` — marks the stage for image export.

See [`../docs/upgrade.md`](../docs/upgrade.md) for the in-dashboard
yt-dlp update flow the image enables.
