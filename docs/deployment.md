# Deployment

Curatables is designed to run on a small self-hosted box on your home
LAN (a Raspberry Pi, an old laptop or a VM) and be reached by family
members the way they reach a networked printer: open a browser, type a
friendly `.local` address, done.

This page covers two things:

1. How to get Curatables to appear at `http://curatables.local/`, with
   no port number and no IP lookup.
2. How to keep it running as a proper system service.

For the full bill of materials (every package, binary, filesystem
path, port, and capability Curatables needs — i.e. what a Dockerfile
author or fresh-install script would read top-to-bottom) see
[`dependencies.md`](dependencies.md). This page assumes those
prerequisites are already installed and focuses on wiring them up.

---

## Quick start (development)

```sh
python run.py              # listens on 0.0.0.0:8080 by default
python run.py --port 80    # requires CAP_NET_BIND_SERVICE (see below)
python run.py --check      # verify dependencies + print versions, then exit
```

### One-shot installer (Debian/Ubuntu/Raspberry Pi OS)

`scripts/install.sh` apt-installs the system packages (ffmpeg,
python3-venv, avahi-daemon, libnss-mdns, ...), installs Deno to
`/usr/local`, builds a virtualenv, `pip install -r requirements.txt`,
and runs `run.py --check` as an acceptance test.

```sh
scripts/install.sh                # try-out install: .venv/ in this checkout, data in ~/curatables-data
sudo scripts/install.sh --systemd # permanent install (see "Option A" below)
scripts/install.sh --dry-run      # validate without touching the system
```

`--dry-run` is the safe way to test the installer from a workstation:
it validates apt package names via `apt-cache policy`, confirms the
Deno install URL is reachable, builds a **throwaway** venv in `/tmp`,
runs `run.py --check` inside it, and tears it down. No sudo, no system
mutation. Use this before shipping installer changes.

If the `zeroconf` library is installed (it's in `requirements.txt`),
you'll see a line like this at startup:

```
INFO curatables: mDNS advertisement registered: http://curatables.local:8080/  (service: Curatables._http._tcp.local.)
```

From another machine on the same network, point a browser at that URL
and you should land on the profile picker.

---

## The printer-like UX has two parts

### Part 1 — mDNS advertisement

Curatables publishes an `_http._tcp.local.` service at startup using
the `python-zeroconf` library. This makes it discoverable:

- **macOS / iOS**: appears in Finder's *Network* sidebar under *Shared*,
  and in any Bonjour browser.
- **Linux**: visible via `avahi-browse -r _http._tcp`.
- **Android**: many file managers and network scanners pick it up;
  Chrome resolves `*.local` addresses directly on most builds.
- **Windows**: needs Apple's Bonjour Print Services installed (comes
  with iTunes or as a standalone download). Without it, `.local`
  resolution won't work on that host — fall back to the raw IP.

Configurable via `config.json`:

```json
{
  "server": {
    "port": 80,
    "mdns_enabled": true,
    "mdns_name": "Curatables"
  }
}
```

If `mdns_enabled` is false, the advertiser is never started. If the
`zeroconf` library isn't installed at all, Curatables logs a notice
(`mDNS advertisement skipped`) at startup and carries on; the rest of the server works fine.

### Part 2 — Binding to port 80 without running as root

Port 80 is a privileged port on Linux — any port below 1024 is — so
a normal user process can't `bind()` it. There are three ways to
fix that; **option A is the one we ship a template for.**

#### Option A: systemd unit with `AmbientCapabilities` (recommended)

`systemd/curatables.service` is installed as-is by
`scripts/install.sh --systemd`. The important lines are:

```ini
User=curatables
Group=curatables
RequiresMountsFor=/home/curatables/curatables-data
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=/opt/curatables/.venv/bin/python /opt/curatables/run.py --port 80 --data-dir /home/curatables/curatables-data
```

`AmbientCapabilities=CAP_NET_BIND_SERVICE` grants the single
capability the process actually needs (binding a privileged port)
without elevating it in any other way. The service runs as the
unprivileged `curatables` user, can write only its data dir
(`ProtectSystem=strict` + `ReadWritePaths`), and drops every capability
*except* the one it uses.

To install, from any checkout (your home directory is fine):

```sh
git clone https://github.com/MadeByTokens/curatables.git
cd curatables
sudo scripts/install.sh --systemd
```

`--systemd`:

- creates the `curatables` service user;
- copies the checkout to `/opt/curatables` (root-owned; the service only
  reads it) and builds `/opt/curatables/.venv`;
- if you already tried curatables from this checkout, copies
  `~/curatables-data` (your password, profiles, videos) to
  `/home/curatables/curatables-data`;
- installs and enables `curatables.service`, the yt-dlp updater
  (`curatables-updater.{service,path}`) and the nightly backup
  (`curatables-backup.{service,timer}`);
- starts the server on port 80.

Re-running the same command from an updated checkout upgrades the
install; it takes a backup first when the service is running. See
[upgrade.md](upgrade.md).

The updater backs the **Settings → Updates → Update yt-dlp** button: the
sandboxed app can't pip-install or restart itself, so the root-owned
helper does it on request. It reads and writes the data dir only as the
service user and runs pip as the owner of the venv. Full flow:
[upgrade.md](upgrade.md).

> **Prefer not to install at all?** A pre-built Raspberry Pi image with
> all of the above already set up is built by
> [`pi-gen/build.sh`](../pi-gen/README.md) — flash, boot, done.

Check the journal to confirm both the listener and the mDNS
advertisement came up:

```sh
sudo journalctl -u curatables -f
```

You should see:

```
curatables server starting on http://0.0.0.0:80
mDNS advertisement registered: http://curatables.local:80/  (service: Curatables._http._tcp.local.)
```

Backups run every night at 03:00 into `/var/lib/curatables-backups`
(see [backup.md](backup.md)).

#### Option B: grant the capability to the Python binary

Quickest thing that can possibly work; fine for a dedicated box, a
slight security widening on a shared machine.

```sh
sudo setcap 'cap_net_bind_service=+ep' "$(readlink -f $(which python3))"
python run.py --port 80
```

The capability applies to that specific `python3` binary, so anything
run with it can bind low ports. Revert with `sudo setcap -r <path>`.

#### Option C: NAT redirect

Leave Curatables on the unprivileged 8080 and redirect traffic
arriving on 80:

```sh
sudo iptables -t nat -A PREROUTING -p tcp --dport 80 -j REDIRECT --to-port 8080
```

Catch: localhost traffic skips `PREROUTING`, so the redirect only
works for clients on other machines. You need an `OUTPUT`-chain rule
for local requests, and you have to persist the rules across reboots
yourself (`iptables-persistent`, `nftables` restore service, etc.).

---

## Media on an external drive (Raspberry Pi + USB disk)

An SD card is small and wears out under constant video writes, so on a
Pi it is worth keeping the library on a USB hard drive or SSD.
On the Pi image, **Box → Drives & backups** in the dashboard does all of
this with buttons (use a drive, move, eject, reconnect, repair, format);
see [parents-guide.md](parents-guide.md#5-usb-drives-for-more-videos).
Underneath, it runs `scripts/setup-external-drive.sh`, which has two
modes. Neither formats anything.

### Media only (any exFAT, NTFS, FAT32 or ext4 drive)

The database, settings and logs stay on the SD card's ext4, the only
filesystem here that keeps SQLite safe through a power cut. Videos,
uploads, thumbnails and channel pictures move to the drive. The drive
may already hold other files (a movie collection to import), and they
are left alone.

```sh
lsblk -f                        # find the partition, e.g. sda1
sudo /opt/curatables/scripts/setup-external-drive.sh --media-only /dev/sda1
```

The script:

- accepts FAT32 with a warning: curatables then refuses any file of
  4 GB or more (the most FAT32 can hold) with a clear message.
- stops curatables.
- makes the empty mount point `/mnt/curatables-media` immutable
  (`chattr +i`), so nothing can be written to the SD card through it
  while the drive is unplugged.
- adds an `/etc/fstab` entry by UUID with `nofail`. exFAT and NTFS
  (the kernel's `ntfs3` driver) get `uid`/`gid`/`fmask`/`dmask` options,
  since they have no Linux owners.
- copies `videos/`, `uploads/`, `thumbnails/` and `channels/` into
  `/mnt/curatables-media/curatables-library/`, checks the copy (file
  count, and bytes on ext4), and keeps the SD-card originals under
  `<data dir>/media.before-external-<date>`.
- sets `storage.media_path` to that folder and adds the mount to
  `storage.import_roots`, so the Import page can list the drive.
- adds a drop-in with `ReadWritePaths=-/mnt/curatables-media`, then
  starts curatables again.

With `storage.media_path` set and that folder missing (the drive is
unplugged or did not mount), the server still starts so the parent can
reconnect the drive from the dashboard, but it stores nothing: kids see a
"videos are resting" screen, the parent sees a notice, and startup crash
recovery, resumed downloads and **Freeing up space** (when it is on) leave the
library alone.
Nothing can be written to the SD card through the immutable mount point.

exFAT and FAT32 have no journal: after an unplug or a power cut, use
**Check & repair** on the Drives page (or `sudo fsck.exfat /dev/sda1`).
An NTFS drive that Windows did not shut down cleanly will not mount until
it is repaired (`ntfsfix -d`, which **Check & repair** runs).

### Whole library (ext4 only)

The drive is mounted **at** the data dir (`/home/curatables/curatables-data`),
so no paths change anywhere and the database moves too.

1. Plug the drive in and find its partition:

    ```sh
    lsblk -f
    ```

    Look for the USB disk (usually `sda`) and its partition (`sda1`).

2. It must be formatted **ext4**. A brand-new drive usually comes as
   exFAT or NTFS; reformat it (this **erases everything on it**):

    ```sh
    sudo mkfs.ext4 -L curatables /dev/sda1
    ```

3. Move the library onto it:

    ```sh
    sudo /opt/curatables/scripts/setup-external-drive.sh /dev/sda1
    ```

The script stops curatables, copies the current library onto the drive
(or reuses a library already on it), keeps the old SD-card copy as
`curatables-data.before-external-<date>`, adds an `/etc/fstab` entry by
UUID with `nofail` (the Pi still boots without the drive), and adds a
systemd drop-in that sets `CURATABLES_REQUIRE_MOUNT=1`.

With that flag, and the unit's `RequiresMountsFor=`, curatables will
not start if the drive is missing. Otherwise it would come up with an
empty library on the SD card, offer first-run password setup to anyone
on the network, and start downloading onto the card. Crash recovery also
refuses to reset the library if most videos suddenly have no file.

Tips for a Pi 4:

- A 2.5" USB hard drive can draw more power than the Pi's USB ports
  supply reliably. Use the official 3 A power supply, an SSD, or a
  drive/hub with its own power. Undervoltage shows up as
  `dmesg | grep -i voltage`.
- Keep **Settings → Advanced → Downloads at once** at 1. Transcoding a
  video to the playback baseline takes minutes on a Pi 4, and jobs queue
  up instead of competing.
- Nightly backups stay on the SD card (`/var/lib/curatables-backups`),
  so the database survives the drive failing and the other way round.

---

## Verifying the mDNS setup

The test we ship covers the advertiser class in isolation with a fake
Zeroconf daemon, so unit tests never touch the network. To confirm
mDNS is actually working end-to-end you need a live run on a real
network. Pick any of these:

1. **Python one-liner** — register a test advertisement from the same
   machine without booting the full server. `start()` and `stop()`
   are coroutines, so the smoke test has to run inside an event loop:

    ```sh
    python -c '
    import asyncio, logging
    logging.basicConfig(level=logging.INFO)
    from app.services.mdns import ZeroconfAdvertiser

    async def main():
        adv = ZeroconfAdvertiser(name="CuratablesSmokeTest", port=8080)
        await adv.start()
        try:
            await asyncio.sleep(60)   # keep it alive for a minute so you can browse
        finally:
            await adv.stop()

    asyncio.run(main())
    '
    ```

2. **`avahi-browse`** (Linux, on the same LAN):

    ```sh
    avahi-browse -r _http._tcp
    ```

    Look for a `Curatables` entry. `-r` resolves the hostname and IP.

3. **`dns-sd`** (macOS, on the same LAN):

    ```sh
    dns-sd -B _http._tcp
    ```

4. **Browser**: open `http://curatables.local/` (or
   `http://curatables.local:8080/` while you're on the dev port). The
   first hit may take a moment while the OS caches the mDNS lookup.

If `curatables.local` doesn't resolve:

- **Routers that don't pass multicast to Wi-Fi.** avahi answers by
  multicast, and some home routers (ISP hubs, and a Netgear R7000 on
  current firmware) never deliver multicast or broadcast to Wi-Fi
  clients: the question reaches the server, the answer never comes
  back, and even `avahi-resolve` times out. The Pi image runs
  `curatables-mdns-unicast.service` (`scripts/mdns-unicast.py`), which
  also sends the server's address straight to whoever asked, addressed
  to the asker's hardware address taken from the question, so it needs
  no ARP broadcast either; that gets through. The unit grants it only
  `CAP_NET_RAW`, for that packet socket. On a manual
  install, copy `systemd/curatables-mdns-unicast.service` to
  `/etc/systemd/system/` and `systemctl enable --now` it. If it still
  fails, reserve the server's address in the router and use the IP.
- **Linux** needs two pieces installed on the *client* machine (which
  is usually also the server for a home setup):

    1. `avahi-daemon` — running in the background to actually answer
       mDNS queries. Install with `sudo apt install avahi-daemon` and
       `sudo systemctl enable --now avahi-daemon`.
    2. `libnss-mdns` — the glibc NSS module that wires `.local`
       lookups through to Avahi. Without it, tools that call the
       standard resolver (`ping`, `curl`, most Python code) will
       fail with "Name or service not known" even though
       `avahi-resolve -n curatables.local` works fine. Install with
       `sudo apt install libnss-mdns`.

    After installing `libnss-mdns`, check `/etc/nsswitch.conf` has a
    `hosts:` line that mentions `mdns4_minimal` or `mdns`:

    ```
    hosts:  files mdns4_minimal [NOTFOUND=return] dns mymachines
    ```

    Test in order of increasing integration:

    ```sh
    avahi-resolve -n curatables.local      # talks to avahi directly
    getent hosts curatables.local          # uses NSS (libnss-mdns)
    ping curatables.local                  # same, plus ICMP
    curl http://curatables.local/          # same, plus TCP
    ```

    If the first succeeds but the rest fail, `libnss-mdns` is the
    missing piece.

- **Windows** needs Apple's Bonjour Print Services installed (ships
  with iTunes or as a standalone download). Without it, `.local`
  resolution won't work on that host — fall back to the raw IP or
  install Bonjour.
- **Android** support depends on the app doing the lookup. Chrome on
  modern Android resolves `.local` names; most third-party browsers
  don't. System-wide mDNS support arrived in Android 12+.

---

## Updating configuration at runtime

The mDNS advertiser reads from `config.server.port` and
`config.server.mdns_name` at app-lifespan start time. Changing those
values in `~/curatables-data/config.json` takes effect on the next
server restart. There is no reload-in-place; restart the service.
