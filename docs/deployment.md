# Deployment

Curatables is designed to run on a small self-hosted box on your home
LAN (a Raspberry Pi, an old laptop, a VM, a NAS with Docker support,
etc.) and be reached by family members the way they reach a networked
printer: open a browser, type a friendly `.local` address, done.

This page covers two things:

1. How to get Curatables to appear at `http://curatables.local/` —
   no port number, no IP lookup, no QR codes.
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

### One-shot installer (Debian/Ubuntu)

`scripts/install.sh` automates the full setup: apt-installs the system
packages (ffmpeg, python3-venv, avahi-daemon, libnss-mdns, ...), pulls
Deno under your user, creates `.venv/`, `pip install -r
requirements.txt`, and finally runs `run.py --check` as an acceptance
test.

```sh
scripts/install.sh                # full install
scripts/install.sh --systemd      # also install the systemd unit + user
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
`zeroconf` library isn't installed at all, Curatables logs a warning
at startup and carries on; the rest of the server works fine.

### Part 2 — Binding to port 80 without running as root

Port 80 is a privileged port on Linux — any port below 1024 is — so
a normal user process can't `bind()` it. There are three ways to
fix that; **option A is the one we ship a template for.**

#### Option A: systemd unit with `AmbientCapabilities` (recommended)

A ready-to-edit unit file lives at `systemd/curatables.service`. The
important lines are:

```ini
User=curatables
Group=curatables
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE
ExecStart=/usr/bin/python3 /opt/curatables/run.py --port 80
```

`AmbientCapabilities=CAP_NET_BIND_SERVICE` grants the single
capability the process actually needs (binding a privileged port)
without elevating it in any other way. The service still runs as the
unprivileged `curatables` user, can't modify system files (thanks to
`ProtectSystem=strict`), and drops every capability *except* the one
it uses.

To install:

```sh
# 1. Drop the code somewhere the service can read
sudo git clone <repo> /opt/curatables
cd /opt/curatables

# 2. Run the installer, which sets everything up and installs the unit
sudo -u $(whoami) scripts/install.sh --systemd

# 3. Edit the unit file to point at /opt/curatables, then enable it
sudoedit /etc/systemd/system/curatables.service
sudo systemctl enable --now curatables
sudo systemctl status curatables

# 4. (optional) Enable the in-dashboard yt-dlp updater
sudo systemctl enable --now curatables-updater.path
```

The installer creates the `curatables` service user, installs apt +
Deno + Python deps, and drops the systemd units. You only need to edit
paths in the unit and enable it.

`--systemd` also installs `curatables-updater.{service,path}`, which back
the **Settings → Updates → Update yt-dlp** button in the dashboard (the
sandboxed app can't pip-install or restart itself, so this root-owned
helper does it on request). Enable the path unit to turn the button on;
if your checkout/data-dir/user differ from the defaults, edit the
`CURATABLES_ROOT`/`CURATABLES_DATA`/`CURATABLES_USER` lines in
`curatables-updater.service` and the watched path in
`curatables-updater.path`. Full flow: [upgrade.md](upgrade.md).

> **Prefer not to install at all?** A pre-built Raspberry Pi image with
> all of the above already set up is built by
> [`pi-gen/build.sh`](../pi-gen/README.md) — flash, boot, done.

Check the journal to confirm both the listener and the mDNS
advertisement came up:

```sh
sudo journalctl -u curatables -f
```

Once the service is up, wire up nightly backups via the systemd timer
and scripts documented in [`backup.md`](backup.md). Restoring from a
backup is a one-command operation; ops should set this up before the
first real content goes in.

When a new release lands, follow [`upgrade.md`](upgrade.md) — backup,
pull, `pip install --upgrade`, `run.py --check`, restart, then
confirm via `/healthz`. Multi-version leaps (e.g. v0.3 → v0.5) are
supported in a single boot; the migrator walks every pending file in
order.

You should see:

```
curatables server starting on http://0.0.0.0:80
mDNS advertisement registered: http://curatables.local:80/  (service: Curatables._http._tcp.local.)
```

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
server restart. There is no reload-in-place path — the project is
pre-launch and the added complexity isn't justified yet.
