# Dependencies

Single source of truth for everything Curatables needs to run. The
layers below are ordered roughly the way a Dockerfile or a fresh
host-install script would install them — base OS packages first,
then language runtimes, then Python deps, then the host-integration
bits that make the "printer UX" work. When you add or remove a
dependency anywhere in the codebase, update the relevant section
here *and* `run.py:check_dependencies` (which probes the runtime
environment at startup).

If you ever build a container image, this file is your bill of
materials. The [Containerization notes](#containerization-notes)
section at the bottom flags the non-obvious decisions you'll have to
make (mDNS needs host networking, port 80 needs a capability,
avahi-daemon inside vs. outside the container).

---

## 1. Language runtimes

| Runtime | Min version | Why | Notes |
|---|---|---|---|
| **Python** | 3.10 | `app/config.py:38` uses PEP 604 `str \| None` union syntax without `from __future__ import annotations`. Older Pythons will fail to import the config module. | Development and type-checking target 3.11 (`pyproject.toml:[tool.mypy] python_version = "3.11"`). CI / prod should run 3.11+. |
| **Deno** | any recent | yt-dlp uses Deno as a JavaScript runtime to run YouTube's player extraction code. Without Deno, YouTube extraction fails with an opaque "extractor" error. | Install script: `curl -fsSL https://deno.land/install.sh \| sh`. Must be on `PATH` when `run.py` starts; `run.py:check_dependencies` warns if missing. |

---

## 2. Base OS / system binaries

These are installed via the host's package manager (`apt`, `brew`,
`apk`, ...) rather than pip.

> **Supported host OS:** Debian/Ubuntu family (including Raspberry Pi
> OS). The shipped deployment story — `scripts/install.sh`, the
> systemd unit, the `CAP_NET_BIND_SERVICE`-on-port-80 and Avahi mDNS
> paths — is Linux-specific. The `macOS (brew)` and `Alpine` columns
> below are a **packager reference** (for future Docker images or
> for devs hacking locally on a Mac), not statements of supported
> host platforms.

| Binary | Debian/Ubuntu | macOS (brew) | Alpine | Why |
|---|---|---|---|---|
| `python3` (≥ 3.10) | `python3 python3-pip` | `python` | `python3 py3-pip` | Server runtime (see above). |
| `ffmpeg` | `ffmpeg` | `ffmpeg` | `ffmpeg` | Merges yt-dlp's separate video+audio streams, processes subtitles, extracts frames for custom thumbnails (`app/services/thumbnails.py:extract_frame`). `run.py:check_dependencies` hard-fails if `ffmpeg` is missing from `PATH`. |
| `ca-certificates` | `ca-certificates` | (bundled) | `ca-certificates` | TLS roots for HTTPS outbound (yt-dlp, zeroconf timeservers, `curl_cffi`). Not a Curatables bug when missing; outbound requests just fail. |

**Not currently needed but may be later**: git (only at install time, and only if you install by cloning — a sdist/wheel release wouldn't need it), a clock sync daemon (mDNS record TTLs care about wall clock, but a host running systemd-timesyncd is fine).

---

## 3. Python runtime dependencies

Authoritative list lives in `requirements.txt`. Rationale for each:

| Package | Purpose | What breaks without it |
|---|---|---|
| `fastapi>=0.100,<1.0` | Web framework: routing, request lifecycle, dependency injection. | Everything — the app can't start. |
| `starlette>=0.29,<2.0` | ASGI toolkit FastAPI is built on; pulled in transitively but pinned directly because we rely on its `TemplateResponse(request, name, context)` signature (added in 0.29, deprecated-old-signature removed in 1.0). | Template rendering 500s on any version that predates the new signature. |
| `uvicorn>=0.20,<1.0` | ASGI server hosting the FastAPI app. | `run.py` can't start the server. |
| `jinja2>=3.1,<4.0` | Template engine for every HTML page (kid UI, parent dashboard). | Every HTML route 500s. |
| `python-multipart>=0.0.6,<1.0` | Form parsing for FastAPI `Form()` params. Not a hard dep of FastAPI itself; forms silently 500 without it. | Login, settings save, add-video, channel edit, sharing import — every POST route fails at request time with a `RuntimeError`. |
| `yt-dlp>=2024.0` | Metadata fetch and video download from YouTube/etc. | `/parent/add` can't fetch previews or download. |
| `curl_cffi>=0.10,<0.15` | Browser TLS impersonation so yt-dlp bypasses YouTube's bot detection without a login. | Listed in `run.py:check_dependencies` as a *warning*, not an error: the server still boots, but anti-bot detection will trip on real YouTube URLs. |
| `itsdangerous>=2.0,<3.0` | Session cookie signing. Required by Starlette's `SessionMiddleware`. | Parent login flow fails at import time. |
| `zeroconf>=0.130,<1.0` | mDNS / `_http._tcp.local.` advertisement via `zeroconf.asyncio.AsyncZeroconf`. | `run.py:check_dependencies` emits a warning; `ZeroconfAdvertiser.start()` logs an "skipped: library not installed" message and returns False; server still boots; `curatables.local` just won't resolve. |
| `reportlab>=4.0,<5.0` | PDF export for shared-curation channels (`app/services/sharing.py:render_pdf`). Pure Python, no system deps. | `run.py:check_dependencies` emits a warning; `pdf_available()` returns False; `GET /parent/channels/{id}/export?format=pdf` returns a 503 page pointing at `pip install reportlab`; `.ytc` + `.txt` export keep working. |
| `prometheus_client>=0.20,<1.0` | Prometheus exposition for `/metrics` (`app/services/metrics.py`). ~64 KB wheel, no transitive deps. | `app/services/metrics.py` imports unconditionally, so a missing install crashes startup; `run.py:check_dependencies` flags it as a hard error. The `/metrics` route itself is opt-in via `config.server.prometheus_enabled` (default off) — when disabled it returns 404 and no counters are collected. |

### Optional vs. required at runtime

`run.py:check_dependencies` is the canonical split. Anything it adds
to `errors` is a hard fail (the process exits non-zero before uvicorn
starts). Anything it adds to `warnings` is degrade-gracefully — the
feature tied to that dep is disabled but the rest of the server
runs.

Current classification (mirror this when editing):

- **Hard-required** (errors): `yt_dlp`, `fastapi`, `uvicorn`, `jinja2`, `python_multipart` (imported as `multipart`), `itsdangerous`, `prometheus_client`, `ffmpeg` on PATH.
- **Warn-and-degrade** (warnings): `deno` on PATH, `curl_cffi`, `zeroconf`, `reportlab`.

---

## 4. Development / testing dependencies

Listed in `requirements-dev.txt` (which starts with `-r
requirements.txt` so it also pulls production deps):

| Package | Purpose |
|---|---|
| `pytest>=7.0` | Test runner. |
| `pytest-asyncio>=0.21` | Runs `@pytest.mark.asyncio` tests, including the ZeroconfAdvertiser async suite. Pinned explicitly in `requirements-dev.txt` — without it, every async test collects as an error. |
| `pytest-cov>=4.0` | Coverage reporting for CI (`pytest --cov=app`). |
| `httpx>=0.24` | Required by `fastapi.testclient.TestClient`. |
| `mypy>=1.0` | Type checker, configured in `pyproject.toml`. |

Not needed in a production container image.

---

## 5. Host-side integration — the "printer UX" bits

These aren't Curatables deps in the import sense, but the
`curatables.local` discovery story falls apart without them. They
matter for container packaging because `--network host` (or another
multicast-capable network mode) is usually the right choice for
this project, and that in turn decides which of these live in the
container vs. on the host.

### On the server

| Package | Debian/Ubuntu | Why |
|---|---|---|
| `avahi-daemon` | `avahi-daemon` | Answers mDNS queries for `curatables.local` on the LAN. Without it, Curatables's `ZeroconfAdvertiser` can still publish (it runs its own mini-responder via the `zeroconf` Python library), but responses can collide with an already-running `avahi-daemon` if one *was* present — running both is fine, running neither means no advertisement. |

### On every Linux client that needs `curatables.local` to work

| Package | Debian/Ubuntu | Why |
|---|---|---|
| `avahi-daemon` | `avahi-daemon` | Resolver backend used by libnss-mdns. |
| `libnss-mdns` | `libnss-mdns` | NSS module that wires glibc's `getaddrinfo()` to Avahi. **Without this, `ping`, `curl`, and most Python code will fail to resolve `.local` names even though `avahi-resolve -n curatables.local` works fine.** The `hosts:` line in `/etc/nsswitch.conf` must mention `mdns4_minimal` or `mdns`; libnss-mdns installs that edit automatically on Debian/Ubuntu. |

### On every Windows client

- **Bonjour Print Services** — Apple's mDNS resolver, ships with
  iTunes or as a standalone installer. Without it, `.local`
  resolution simply doesn't work on that host.

### On every Android client

- Chrome on Android 12+ resolves `.local` names natively. Most
  third-party browsers and apps don't. Documented in
  `docs/deployment.md`.

---

## 6. Runtime filesystem layout

Curatables creates and writes to these paths under `config.storage.path`
(default `~/curatables-data/`). See `app/config.py:ensure_directories`
for the canonical list — update this table when it changes.

| Path | Purpose | Volume-mount in a container? |
|---|---|---|
| `db/curatables.db` | SQLite database (schema in `app/db/schema.sql`). | Yes — persist across container restarts. |
| `videos/<id>/` | yt-dlp-downloaded video file, thumbnails, subtitle tracks. | Yes — these are big and expensive to re-download. |
| `uploads/<id>/` | Parent- and kid-uploaded video files, normalized to the playback baseline (H.264/AAC ≤720p30 +faststart) on finalize, same as downloads. | Yes — user-generated content. |
| `uploads/.tmp/` | In-progress resumable uploads. Swept at startup by `app/main.py` via `UploadService.sweep_abandoned(ttl_hours=24)`. | Yes — interrupted uploads can resume across restarts. |
| `thumbnails/custom/` | Reserved for parent-uploaded custom thumbnails (pre-created). | Yes. |
| `thumbnails/profiles/<profile_id>/` | Per-kid custom thumbnail overrides. Created on demand (not by `ensure_directories`). | Yes — user-generated content. |
| `channels/<channel_id>/` | Kid channel art (banner, icon). Created on demand (not by `ensure_directories`). | Yes — user-generated content. |
| `logs/` | Server access + error logs. | Optional — useful for post-mortems, safe to discard. |
| `config.json` | Parsed by `app/config.py:load_config` at startup. Lives at the data-dir root. | Yes — contains the hashed parent password, session secret, and any user-tuned settings. |

Container images should mount `/curatables-data` (or whatever path
you pick) as a single volume and pass `--data-dir /curatables-data`
to `run.py` so every subdirectory lands under the mount.

---

## 7. Runtime capabilities and ports

| Thing | Detail |
|---|---|
| **Default port** | `8080` (see `app/config.py:ServerConfig.port`). No privilege required. |
| **Port 80** | Optional. Requires `CAP_NET_BIND_SERVICE` on Linux. The shipped `systemd/curatables.service` unit grants this via `AmbientCapabilities=CAP_NET_BIND_SERVICE` — see `docs/deployment.md` for the full "unprivileged user + one capability" recipe. In a container, pass `--cap-add NET_BIND_SERVICE` (docker) or the equivalent (podman, k8s `securityContext.capabilities.add`). |
| **mDNS** | Uses UDP/5353 on the `224.0.0.251` multicast group. Outbound and inbound. `zeroconf.asyncio.AsyncZeroconf` opens this automatically; no explicit port config in `config.json`. |
| **Outbound HTTPS** | Required for yt-dlp to reach YouTube/etc. Containers that egress-filter must allow at least `*.youtube.com`, `*.googlevideo.com`, `*.ggpht.com` (thumbnails). |
| **Cache sweep cadence** | `config.storage.cache_cleanup_interval_minutes` (default 60). Background asyncio task in `app/main.py` lifespan calls `StorageService.evict_expired` on this interval. Set to 0 to disable the sweep (the on-demand rehydrate path keeps working either way). |

---

## 8. Non-root user

`systemd/curatables.service` runs as `curatables:curatables` — an
unprivileged system user that owns `/home/curatables/curatables-data`
and `/opt/curatables`. The container image should do the same:

- Create a `curatables` user in the Dockerfile (`adduser --system --home /curatables-data --shell /sbin/nologin curatables`).
- `chown -R curatables:curatables /opt/curatables /curatables-data`.
- `USER curatables` before `ENTRYPOINT ["python", "/opt/curatables/run.py"]`.

**Why non-root inside the container?** Because Curatables drives
yt-dlp and curl_cffi, which fetch arbitrary URLs off the open
internet on the parent's behalf. Anything that parses untrusted
remote input is a container-escape risk surface, and "escape lands
you on the `curatables` uid" is strictly less bad than "escape
lands you on uid 0 inside the container (which, depending on the
host, may be easier to pivot into host-root via a kernel bug or a
bind-mount misuse)".

**Does non-root break port 80 inside the container?** No — see the
[Port 80 binding](#port-80-binding) subsection below. The short
version: pass `--cap-add NET_BIND_SERVICE` on `docker run`. Docker
19.03+ promotes that into the ambient capability set for non-root
containers, so the unprivileged user inherits it and can call
`bind(80)` directly. You do **not** need `setcap` on the Python
binary inside the image, and you do **not** need to run as root.

The host-side port mapping (`-p 80:80`) is always handled by the
Docker daemon, which already runs as root on the host, so there is
no host-level privilege work to do either.

---

## Containerization notes

The non-obvious decisions a Dockerfile author has to make, none of
which the rest of this document dictates:

### mDNS + container networking

mDNS advertisements travel over a multicast group, and Docker's
default bridge networking NATs outbound multicast in a way that
breaks both publication and discovery. Practical options:

1. **`--network host` (recommended for home-lab use)**. The container
   shares the host's network namespace, so the advertisement goes out
   on whatever interface the host has. Trivial, "just works", kills
   any hope of running two Curatables instances on the same host.
2. **Macvlan / ipvlan**. Gives the container its own LAN IP. mDNS
   works. More complicated to configure, incompatible with Docker
   Desktop for Mac/Windows.
3. **Give up on mDNS inside containers** and run `avahi-daemon` on
   the host in reflector mode, pointing at the container IP. Works
   but requires host configuration that's out of scope for a
   "clone and `docker run`" experience.

Most self-hosted home-lab images for similar tools (Home Assistant,
PiHole, Jellyfin) default to `--network host` for this exact reason;
Curatables should too.

### Port 80 binding

Three layers are at play; each answers a slightly different version
of "who needs privilege to bind port 80":

1. **The host-side mapping** (`docker run -p 80:80 ...`) is handled
   by the Docker daemon, which already runs as root on the host.
   **You do not need host-side `setcap` or `sysctl`** — it just
   works.

2. **The container-side bind** (`run.py --port 80` inside the
   container) is controlled by the effective capabilities of the
   process that calls `bind()`. Docker's default capability set
   includes `CAP_NET_BIND_SERVICE`, so a container that runs as
   root can bind port 80 with zero extra flags.

3. **Non-root containers**: if the Dockerfile uses a `USER` directive
   (which it should — see [§8 Non-root user](#8-non-root-user)), the
   default capability set is *inherited* but not *ambient*, so the
   unprivileged user still can't call `bind(80)`. The fix is one
   flag on `docker run`:

    ```sh
    docker run --cap-add NET_BIND_SERVICE -p 80:80 curatables
    ```

    Docker 19.03+ promotes `--cap-add NET_BIND_SERVICE` into the
    *ambient* capability set when the container runs as non-root,
    so the unprivileged user in the container inherits it and can
    call `bind(80)`. This is the exact same mechanism the shipped
    `systemd/curatables.service` uses via `AmbientCapabilities=` —
    just spelled differently.

**Decision for the Curatables image**: run as `USER curatables`
(non-root) and require the caller to pass
`--cap-add NET_BIND_SERVICE`. Rationale:

- Matches the systemd unit we already ship, so anyone who already
  knows the non-container deployment story already knows this one.
- One extra `docker run` flag is cheap documentation-wise, and
  we can bake it into a `docker-compose.yml` example so most
  users never type it by hand.
- A container escape from an unprivileged uid is strictly less bad
  than an escape from container-root. For a tool that pulls
  arbitrary URLs off the internet (yt-dlp, curl_cffi), that
  tradeoff matters even on a home LAN.

**Don't** run the image as root "for simplicity" — the whole point
of the non-root + capability split is that the process has exactly
the one privilege it needs and nothing else.

**Alternatives** (documented for completeness, not recommended):
- Run `USER root` / omit `USER` and bind 80 directly. Simpler
  Dockerfile, worse security posture. Valid for a throwaway dev
  container, not a home-lab default.
- Keep the server on 8080 inside the container and map
  `-p 80:8080` on the host. Works, but breaks the "one port in both
  places" invariant that every other section of the docs assumes
  — the mDNS advertisement inside the container would publish port
  8080, so `http://curatables.local/` from a client would fail
  even though the container bind did not.
- Put a reverse proxy (Caddy, nginx) on the host and leave the
  container on 8080. Fine if you already run a reverse proxy for
  other services, overkill if you don't.

See `docs/deployment.md` for the non-container version of the same
tradeoff (systemd + `AmbientCapabilities=CAP_NET_BIND_SERVICE`).

### Avahi-daemon: inside or outside?

If the host already runs `avahi-daemon` (most desktop Linux distros
and Raspberry Pi OS do), you **do not** need to run it inside the
container. The `zeroconf` Python library publishes its own mini
responder which coexists fine with `avahi-daemon` on the host as
long as both are on the same IP/interface — which `--network host`
guarantees.

If the host does not run `avahi-daemon`, the container's `zeroconf`
advertisement still publishes, and Linux clients with
`libnss-mdns` + `avahi-daemon` installed will still resolve
`curatables.local`. So in practice: don't bundle `avahi-daemon` in
the image.

### Signals and PID 1

`uvicorn` handles `SIGINT`/`SIGTERM` correctly as PID 1, which is
what you get when `ENTRYPOINT` invokes `run.py` directly. You do
**not** need `tini` or `dumb-init` unless you're also launching
subprocess children that orphan on the uvicorn process (currently
none — yt-dlp runs synchronously inside the download worker).

### Deno

Two options, both fine:
1. Install Deno from the upstream script inside the Dockerfile:
   `RUN curl -fsSL https://deno.land/install.sh | sh`. Lands at
   `/root/.deno/bin/deno`; add to `PATH`.
2. Copy the prebuilt binary from the official `denoland/deno` Docker
   image:
   ```
   FROM denoland/deno:alpine AS deno
   ...
   COPY --from=deno /usr/bin/deno /usr/local/bin/deno
   ```

Option 2 is smaller and reproducible. Deno's binary is ~100 MB
uncompressed either way — worth flagging for an Alpine image where
that's a large fraction of the total.

### CAP_NET_BIND_SERVICE on Podman rootless

Podman's rootless mode also needs `--cap-add NET_BIND_SERVICE` to
bind port 80, but there's a second gotcha: by default, rootless
users can't bind to privileged ports at all. Set
`net.ipv4.ip_unprivileged_port_start=80` in `/etc/sysctl.conf` on
the host, or map a high host port to container 80 instead. Worth
mentioning in any podman-specific deployment doc.

---

## Installer script

`scripts/install.sh` is the one-shot installer for Debian/Ubuntu and
consumes this file's rules at build time: the apt package list in the
script mirrors §2, and it runs `run.py --check` as its acceptance
test (which mirrors §3 + the `ffmpeg`/`deno` rows from §2). If you
add or remove a runtime dep, update **both** `run.py:check_dependencies`
and `scripts/install.sh`'s `APT_PACKAGES` in the same commit.

`scripts/install.sh --dry-run` is how to test installer changes from a
workstation without root: it resolves apt package names via
`apt-cache policy`, probes the Deno URL, and runs the full venv +
`pip install` + `run.py --check` cycle inside a throwaway tmpdir that
is cleaned up on exit.

---

## Keeping this file in sync

This document is load-bearing for future packaging work, so stale
entries are worse than missing ones. Rules:

1. When you add a dep to `requirements.txt`, add a row to
   [§3 Python runtime dependencies](#3-python-runtime-dependencies)
   with its rationale and the graceful-degradation story.
2. When you add a new subprocess or system binary call, add a row to
   [§2 Base OS / system binaries](#2-base-os--system-binaries) and
   update `run.py:check_dependencies` so missing installs are caught
   at startup.
3. When you change `app/config.py:ensure_directories` or the data
   layout, update
   [§6 Runtime filesystem layout](#6-runtime-filesystem-layout).
4. When you add a network capability, mDNS record, or port, update
   [§7 Runtime capabilities and ports](#7-runtime-capabilities-and-ports).
5. When you change the runtime user, update
   [§8 Non-root user](#8-non-root-user) and the systemd unit in
   lockstep.
