# Curatables Threat Model

> Companion to [`SECURITY.md`](../SECURITY.md). This file describes the
> trust boundaries, the attack surface, and — critically — the risks
> Curatables does **not** mitigate today. It is intentionally honest:
> "we don't defend against X" is a feature of a threat model, not a bug.
> The plain-language version for parents is
> [`safety-model.md`](safety-model.md).

## Deployment shape

Curatables is designed for one specific operating environment:

- A single Linux box on a home LAN: the Raspberry Pi 4B appliance image
  (built with [`pi-gen/`](../pi-gen/README.md), Raspberry Pi OS Lite
  trixie) or a Debian/Ubuntu machine installed with
  `scripts/install.sh --systemd`.
- Reachable on that LAN only, typically as `http://curatables.local/`
  via mDNS, plain HTTP on port 80.
- Media optionally on an external ext4 USB drive mounted at the data
  dir (`scripts/setup-external-drive.sh`).
- One administrator (the parent) with shell access to the box.
- Zero, one, or more kid profiles, used on family devices on the same
  LAN.
- **No** internet exposure. Egress is one-way: Curatables fetches from
  upstream sites (YouTube et al.) via `yt-dlp`. The server is not meant
  to be reverse-proxied onto the public internet.

Anything outside this shape (cloud VM, Docker on a multi-tenant box,
public reverse proxy, hostile LAN) is **outside the supported threat
model**. Some of those configurations may still be safe; others
definitely aren't. We'd rather say so than imply blanket safety.

### Install layout

Both the image and `install.sh --systemd` produce the same layout:

| Path | Owner | Notes |
|------|-------|-------|
| `/opt/curatables` (code + `.venv`) | root | Read-only to the service. `chown -R root:root` after every install/build. |
| `/home/curatables/curatables-data` | `curatables` | Data dir: `config.json`, SQLite DB, videos, thumbnails, update flag files. |
| `/var/lib/curatables-backups` | `curatables`, mode 0700 | Nightly snapshots (systemd `StateDirectory`), off the media drive. |
| `/usr/local/bin/deno` | root | Pinned release from GitHub, SHA-256 checked by `scripts/install-deno.sh`. |

`curatables.service` runs as the unprivileged `curatables` user with
only `CAP_NET_BIND_SERVICE` (port 80), `NoNewPrivileges`,
`ProtectSystem=strict`, `ProtectHome=read-only` with the data dir as the
single `ReadWritePaths`, `PrivateTmp`, and restricted address families.
`MemoryDenyWriteExecute` is off because Deno may JIT.

The image enables SSH but bakes no login password: the flasher sets the
login user in Raspberry Pi Imager. Wi-Fi credentials are read from
`curatables-wifi.txt` on the FAT boot partition by
`curatables-firstboot.service` on every boot. Once the network is
configured the service blanks `ssid` and `psk` in that file;
NetworkManager keeps them in its root-only store on the ext4 partition.
The password is removed rather than replaced by a `wpa_passphrase`
hash, because that hash is the WPA key itself and joins the network
just as well.

## Trust boundaries and roles

| Role         | Trust level | Capabilities                             |
|--------------|-------------|------------------------------------------|
| Parent       | Trusted     | All routes under `/parent/*`, all kid views, server shell. |
| Kid (profile)| Semi-trusted| Kid UI for the selected profile: videos in visible channels, and per-profile switches for reactions, comments, tags, customize and uploads (switched-off features are refused server-side by `require_kid_feature`). Cannot reach `/parent/*`. |
| Anonymous    | Untrusted   | `/profiles` picker, `/parent/login`, `/parent/setup` (first run only), static assets, `/healthz`, `/metrics` (only if enabled). Sees no videos, thumbnails, subtitles or channel art. |
| LAN attacker | Adversary   | Can reach any port the box opens. Cannot read disk or process memory. Can sniff plaintext HTTP. |
| WAN attacker | Out of scope| Should not be able to reach the box at all. |
| Service user (`curatables`) | Contained | What a compromised app process (e.g. a malicious extractor) gets: the data dir, network egress, and the ability to request a yt-dlp upgrade. |
| Root updater | Trusted, narrow | See [The root-run updater](#the-root-run-updater). |

### Viewer resolution and session separation

`app/dependencies.py:get_viewer` gives a selected kid profile priority
over a parent login; parent routes use `require_parent`, which needs a
valid parent session. The two never coexist in one session:

- Picking a kid profile (`/profiles/select`, `/profiles/pin`, or the
  single-profile auto-select on `/profiles`) drops `parent_authenticated`
  from the session, so "Switch" from kid mode lands on the picker, not
  the dashboard.
- Parent login and setup (`AuthService.start_parent_session`) drop
  `profile_id`.
- A parent session is valid only while its `parent_epoch` matches
  `config.parent.session_epoch`. Changing the password bumps the epoch,
  which signs out every other parent session (the session cookie is
  stateless, so this is the only revocation mechanism). The device that
  changed the password gets a fresh session.

### Visibility policy

`ContentService.visible_channel_ids` / `can_view` /
`channels_for_viewer` is the single policy for every list, search,
watch and media path:

- Parent: everything.
- Kid: parent channels allowed by the profile, plus channels the kid
  owns; a sibling's channel only if explicitly whitelisted. Videos
  without a channel are not visible to kids.
- Anyone else: nothing.

Restriction is an explicit flag (`profiles.channels_restricted`), so a
restricted profile whose whitelisted channels were all deleted sees
nothing rather than everything. Search with an empty visible set
returns nothing. `/media/video`, `/media/thumb`, `/media/subs` and
`/media/channel/*` check the viewer; the kid channel nav lists only
visible channels, and `/channel/{id}` for a hidden channel redirects
home. Kid search is local to the library and can be disabled per
profile.

A video reaches kids only after the parent ticks "I have already fully
watched this video" (checked server-side on approval) and only while
`status='active'` and downloaded.

## Authentication model

### Parent password

- Stored in `config.json` as `salt_hex:hash_hex`, PBKDF2-HMAC-SHA256,
  **600,000 iterations**, 16-byte random salt
  (`app/services/auth.py`). Verified with `hmac.compare_digest`.
- Minimum length **8 characters** (setup and change).
- Set on first run via `/parent/setup`. While no password exists, every
  route except `/parent/setup`, `/static` and `/healthz` redirects to
  setup — **whoever reaches it first on the LAN becomes the parent.**
  Setup only accepts a password during the first
  `parent.setup_window_minutes` (default 30) after the server starts;
  after that it shows "Setup has timed out" until the server is
  restarted, so a box left unconfigured is not open indefinitely.
- `/parent/login` allows **10 failed attempts per client IP, and 50 from
  all clients together, per 5 minutes** (429 after that). Failures are
  stored in the database (`login_failures`), so a restart does not reset
  them; a successful login clears that client's count. The total cap
  means many addresses on one LAN host don't add up to more tries, at
  the cost that someone flooding wrong passwords can lock the parent
  out for a few minutes.
- Changing the password requires the current one and is not
  rate-limited (it already needs a parent session).
- Forgotten password: shell access to the box (edit `config.json`) is
  the recovery path; there is no reset over the network.

### Sessions

- Starlette `SessionMiddleware`: signed (not encrypted) cookie, secret
  in `config.json`, `SameSite=Strict`, `HttpOnly`, no `Secure` flag
  (plain HTTP). Lifetime `parent.session_timeout_hours` (default 24),
  for kid and parent sessions alike.
- Logout is a POST with a CSRF token, so another page cannot sign the
  parent out; a GET of `/parent/logout` just goes to the dashboard.

### Kid PIN

PINs are a sibling barrier, not an access-control boundary.

- 4–6 digits, or a picture PIN of 3 picks from 9 pictures (stored as
  the 3 digits the pictures stand for, so 729 combinations), stored as
  PBKDF2-HMAC-SHA256 (100,000 iterations, random
  salt; migration 0005 converted existing rows), compared with
  `hmac.compare_digest`. A legacy plaintext value would still verify.
- **5 attempts per profile per 5 minutes** (in memory). That bounds an
  automated guesser to about 3.5 days on average for a 4-digit PIN —
  slow, not impossible. Because the limit is per profile, someone else
  can also lock a kid out for 5 minutes by mistyping.
- The parent profile form never echoes the PIN back.
- An empty PIN means "no PIN": selecting the profile is one POST, and a
  household with a single PIN-less profile is auto-selected on
  `/profiles`. In that case any device on the LAN gets that kid's view.
- The gate sits on the picker. Once a session has `profile_id`, that
  profile's views render until `/profiles/switch`, parent login, or
  cookie expiry.

Households that need real isolation between siblings should use one
device per child (with device-level locking) rather than the PIN.

### CSRF

- Per-session nonce in the signed session; tokens are signed with
  `itsdangerous` and live as long as the session
  (`session_timeout_hours`). Checked on every `POST/PUT/PATCH/DELETE`
  from the `csrf_token` form field or the `X-CSRF-Token` header
  (`app/middleware/csrf.py`). Rejection is a plain 403.
- Exempt prefixes (`CSRF_EXEMPT_PREFIXES`):
  - `/parent/setup`, `/parent/login`, `/profiles/select`,
    `/profiles/pin` — session-establishment endpoints.
  - `/api/log` — player telemetry; it records only `video_view` /
    `video_play` / `video_complete` from a kid who can see the video.
  - `/parent/upload` — the vendored `tus-js-client` sends no token. This
    prefix also covers `/parent/upload/finalize/*`. These routes require
    a parent session; `SameSite=Strict` is what protects them.
- `SameSite=Strict` alone defeats cross-site CSRF in current browsers;
  the token is defense in depth.

## Child-facing attack surface

### Uploads from a kid profile

`POST /upload` (if the profile's upload switch is on). Mitigations:

- Body ceiling `storage.max_kid_upload_bytes` (default 500 MB) enforced
  by `BodySizeLimitMiddleware`, which is the outermost middleware and
  checks `Content-Length` and streamed bytes before anything (including
  CSRF form parsing) reads the body.
- `ffmpeg`-probed codec allow-list; unsupported files are rejected.
- Content-hash dedup; free-disk guard.
- An upload into a **shared (parent) channel** is stored with
  `status='review'` and stays invisible to every kid until the parent
  approves it ("Needs your attention" on the dashboard). An upload into
  the kid's **own** channel goes live immediately for that kid (and
  for siblings only if the parent whitelisted that channel for them).

Not covered: decoder vulnerabilities in `ffmpeg` and the browser media
stack. Curatables does not sandbox `ffmpeg` beyond the service unit;
keep the system patched.

### Kid channels, pictures and comments

- Channel names are unique: a kid can't create or rename onto an
  existing name, and a parent channel can't be created onto a kid's
  private one.
- Channel colour must be `#rrggbb` (it is rendered into a `style`
  attribute); avatar, theme and search mode are validated against
  known values.
- Banner, icon, and custom-thumbnail uploads (kid and parent) must be
  JPEG/PNG/WebP/GIF by magic bytes and at most 5 MB; the stored
  extension comes from the bytes, and they are served with the sniffed
  image type. Picture-upload and import routes have a 6 MB body limit;
  everything else not listed above has 1 MB.
- Comments: 500 characters, 10 per minute per kid, visible to family
  members who can see the video's channel, replies filtered the same
  way, and a reply must belong to the same video. They are not
  pre-moderated: the parent sees and can delete them afterwards.
  Reactions and comments check visibility. Jinja autoescape covers all
  user text; do not disable it.
- Parent delete confirmations use a `data-confirm` attribute read by
  `/static/parent/app.js`, so kid-controlled names never end up inside
  inline JavaScript.

### Re-downloads after eviction

Cache-mode videos are evicted (least recently watched first) and
downloaded again from the approved URL when a kid taps them. The URL may
now serve something else. When the re-downloaded file's length differs
from the approved length by more than 10 s **and** 5 %, the video goes
back to `status='review'` with a note. This is a length check only: a
same-length replacement, or a video whose length is unknown or can't be
probed, is not caught. Videos marked **keep forever** are never evicted.

### PIN bypass between siblings

With no PIN on a sibling's profile, a kid can switch into it from the
picker. This never unlocks `/parent/*` (selecting a profile drops any
parent login) or PIN-protected profiles. It does expose that sibling's
visible channels and misattributes watch history in `/parent/stats`.

### The kid device itself

The kid UI has no outbound links, embeds, ads or "up next", but the
tablet still has a browser and an address bar. Locking the device to
Curatables (iOS Guided Access, Android screen pinning, a kiosk browser)
is the parent's job; Curatables cannot enforce it.

## Parent-facing attack surface

### Session theft on the LAN

Plain HTTP: a passive sniffer on the LAN can capture the parent cookie
and replay it until it expires or the password is changed (which bumps
the session epoch). `HttpOnly` blocks script access; `SameSite=Strict`
blocks cross-site use. There is no TLS option today. Don't run the
dashboard on shared networks (apartment Wi-Fi, hotspots).

A parent session left open on a shared device is full parent access to
anyone holding that device. Log out, or pick a kid profile (which drops
the login) before handing it over.

### Browser-side hardening

Every response carries `X-Content-Type-Options: nosniff`,
`X-Frame-Options: DENY` and `Referrer-Policy:
strict-origin-when-cross-origin`, and a `Content-Security-Policy`
(`app/main.py`) that allows scripts only from the app itself
(`script-src 'self'`, no inline code), frames only over https (the video
embeds in the parent preview), and no plugins, foreign form targets or
framing. Inline style attributes are still allowed. All page scripts
live in `app/static/`; a test fails if a template gains an inline
`<script>`, an `on…=` handler or a `javascript:` URL. Jinja autoescape
is the first line against XSS; the CSP stops injected markup from
running script if that ever fails.

### Data-dir relocation

`/parent/settings/move-data` (parent only) is refused when the server
was started with `--data-dir` (every systemd install), when the target
is inside the current dir, not absolute, not empty, lacks free space, or
while downloads are running. After a move it leaves a pointer
`config.json` holding only `storage.path` at the old location. If that
pointer's target is missing at the next start (e.g. an unmounted drive),
the pointer is loaded as the config, which has no password, and
first-run setup opens to the LAN. For external drives use
`setup-external-drive.sh` instead.

### External drive and unmounted-drive guards

An external drive that fails to mount leaves an empty directory on the
SD card. Without guards the app would start a fresh library there and
offer first-run setup to anyone on the LAN. Guards:

- `RequiresMountsFor=` the data dir on the server and backup units.
- `setup-external-drive.sh` mounts by UUID with `nofail` and installs a
  drop-in setting `CURATABLES_REQUIRE_MOUNT=1`; `run.py` then refuses
  to start unless the data dir is a mount point.
- Crash recovery refuses to reset the library when at least 5 ready
  videos and at least half of all ready videos are missing at once,
  and logs why, instead of marking them all for re-download.

Without `CURATABLES_REQUIRE_MOUNT=1` (a drive mounted by hand), only the
crash-recovery guard applies, and it protects the video rows, not
`config.json`.

### Backups

`scripts/backup.sh` (nightly via `curatables-backup.timer`, as the
service user) writes a SQLite online-backup snapshot of the database
plus a copy of `config.json` (parent password hash, session secret, all
settings) to `/var/lib/curatables-backups`, with `umask 077` (files
0600, directory 0700), keeping 14. Videos are not included. Snapshots
are not encrypted; anyone with the session secret can forge a parent
session cookie, so treat them as parent-secret material. Restoring
them restores those secrets too.

## The root-run updater

The sandboxed service can't `pip install` or restart itself. The
in-app "Update yt-dlp" button writes `update-request.json` in the data
dir; `curatables-updater.path` notices it and starts
`curatables-updater.service`, which runs
[`scripts/updater.sh`](../scripts/updater.sh) **as root, unsandboxed**.
This is the one place data controlled by the service user crosses into
root.

What bounds it:

- It does one fixed thing: `pip install --upgrade 'yt-dlp>=2024.0'` in
  `/opt/curatables/.venv`, then `systemctl restart curatables`. The
  flag's only field read is `kind`, and anything but `yt-dlp` is
  rejected. Nothing from the flag is executed.
- Every read and write inside the service-owned data dir (parsing the
  flag, writing `update-result.json` atomically) runs as the service
  user via `runuser`, so a planted symlink can't redirect a root
  write.
- pip runs as the owner of the venv. Both install paths keep
  `/opt/curatables` and its venv root-owned, so the code root runs is
  code only root can modify.
- `TimeoutStartSec=600`.

What remains:

- A compromised service process can trigger upgrades at will (fetching
  the latest yt-dlp from PyPI); it cannot choose the package or version.
- Root imports the freshly installed `yt_dlp` (to report its version),
  so a malicious yt-dlp release on PyPI would run as root once, not
  only inside the sandbox.
- If an operator builds the venv as another user, the script still
  runs that venv's `python` as root. Keep `/opt/curatables` root-owned.

## Supply chain: `yt-dlp`, Deno, `ffmpeg`

`yt-dlp` ships ~1,800 extractors, each arbitrary Python code, run
inside the server process. It is installed unpinned from PyPI
(`requirements.txt` sets a floor) and upgraded by the updater; we do not
audit extractors. For YouTube, yt-dlp hands the site's player
JavaScript to Deno. `ffmpeg` parses every downloaded and uploaded file.
All of these parse untrusted remote media and metadata.

Mitigations: only the parent can submit URLs (`/parent/*`); a kid with
the wishlist switch on can only send a search phrase (`ytsearch` of up
to 100 characters, rate-limited) and an 11-character YouTube video id,
from which the server builds the watch URL itself; downloads
use `noplaylist` and `storage.max_download_bytes` (default 4 GB); the
service unit confines a compromise to the service user (data dir,
network egress, update requests). There is no further sandbox.

Remote thumbnails are fetched only from `http(s)` URLs whose host
resolves to public internet addresses only (`app/services/safe_fetch.py`;
private, loopback, link-local, CGNAT and multicast ranges are refused),
following at most 3 redirects, each checked the same way. They are capped
at 5 MB and kept only if the bytes are an image. The connection goes to
exactly the addresses that passed the check (no second DNS lookup), so a
name that changes its answer between the check and the connection (DNS
rebinding) cannot reach the home network. IPv4 addresses are tried
first, with a 4-second connect timeout per address.

### Import from a drive

Parent-only (`/parent/import`, CSRF on every POST). The page lists files
only under `storage.import_roots`; every path is resolved and must stay
inside its root, symlinks, hidden files and Windows system folders are
skipped, and the library's own media and data folders are never listed,
even when configured as a root. A queued job re-checks its file is still
inside a root and unchanged (size and mtime) before reading it. Sidecar
pictures and subtitles are only taken from the video's own folder; `.nfo`
files with DTD or entity declarations are ignored, and picture bytes must
pass the magic-byte check.

Each job works in `uploads/.tmp/import-<id>/`. ffmpeg runs in its own
process group with its stderr in a file; a cancel or a 2-minute stall
stops the group and the folder is deleted. Before the short final commit
nothing outside that folder is written, and the source file is only
removed ("move") after the video row exists. On restart, running jobs are
queued again and unclaimed job folders are deleted.

Lookups (optional, `imports.lookups_enabled`) send only the title and
year the parent typed, or a link they pasted, to `*.wikipedia.org`,
`www.wikidata.org` (and `upload.wikimedia.org` for the picture),
`api.themoviedb.org` / `image.tmdb.org` when a TMDB key is set, YouTube
through yt-dlp, or the linked page. All of it goes through `safe_fetch`;
results are plain text, shown to the parent, and saved only when the
parent picks one.

Residual: a parent who adds a folder to `import_roots` lets the parent
page list (not read) every video file under it; a hostile media file
reaches ffprobe/ffmpeg, as with uploads.

### Media on a separate drive

With `storage.media_path` set (`setup-external-drive.sh --media-only`)
the database, config and logs stay on the SD card and only media goes to
the drive, which may be exFAT or NTFS (mounted with the service user's
uid/gid). If the configured media folder is missing at startup, the
server refuses to start instead of creating it on the SD card; the empty
mount point is `chattr +i`, so no process can write to the SD card
through it while the drive is unplugged; a drop-in adds
`RequiresMountsFor=` and `ReadWritePaths=` for the mount. Moving the
library from the dashboard is refused in this mode.

### Kid wishlist (YouTube search)

Off per profile (`allow_wishlist`, migration 0011) until the parent
switches it on. `/wishlist` runs `ytsearchN:` on the server and filters
results by `config.wishlist`: blocked words (query, title, channel;
a query with one is refused before any request to YouTube), an
optional channel allowlist, a maximum duration, no live or upcoming
streams, YouTube ids only. Thumbnails are fetched by the server
(`safe_fetch`) and served from `/wishlist/thumb/<id>` only for ids this
profile was just shown or asked for (kept in the session under the
profile id). `POST /wishlist/ask` takes only a video id, re-fetches its
metadata and re-applies the rules before storing a pending
`wishlist_requests` row. Nothing is downloaded or playable until the
parent adds it through the normal preview, which marks the request
approved; a rejected video never reappears in that kid's results.

Residual: titles and thumbnails from an unfiltered search reach the
kid before any human sees them; the word list and allowlist are the
only filter (no SafeSearch, no classifier yet).

## Defense-in-depth: what's in place

- PBKDF2-SHA256 parent password (600k) and PINs (100k), constant-time
  compares, 8-char minimum password.
- Login and PIN rate limits; session epoch revocation on password
  change; kid/parent session separation.
- Fail-closed channel visibility; anonymous viewers see nothing.
- Parent "watched it all" approval gate; kid uploads to shared channels
  held for review; changed-length re-downloads held for review.
- Per-session CSRF tokens with a short, audited exempt list;
  `SameSite=Strict`, `HttpOnly` signed session cookies.
- Outermost per-route body size limits.
- Image validation by magic bytes; `#rrggbb` channel colour; enum
  validation; `data-confirm` instead of inline confirm scripts.
- ffmpeg codec allow-list on uploads; comment rate limits.
- Hardened systemd unit; root-owned code; updater touches the data dir
  only as the service user.
- Unmounted-drive guards (whole-library and media-only modes); nightly
  0600 backups off the media drive.
- Imports confined to configured folders; cancellable jobs that clean up
  after themselves; outbound fetches pinned to checked public addresses.
- Forward-only schema migrations.

## Residual risks and known gaps

| Gap | Severity | Status |
|-----|----------|--------|
| No TLS; parent cookie sniffable and replayable on the LAN | Medium (LAN-dependent) | Accepted for LAN-only use |
| First-run setup: anyone on the LAN who gets there first, within 30 minutes of the server starting, sets the parent password | Low | Mitigated by the setup window; set the password right after first boot |
| PIN is a sibling barrier (4-digit: ~3.5 days average under the rate limit), and a PIN-less single profile is auto-selected for any LAN device | Low | Documented |
| PIN and comment rate limits are in memory and reset on restart; a flood of wrong parent passwords locks everyone out for 5 minutes | Low | Documented |
| A parent session left open on a shared device is full parent access | Medium | Documented; log out or pick a kid profile |
| Kid device still has a browser; device lockdown (Guided Access / screen pinning / kiosk) is up to the parent | Medium | Out of scope |
| `yt-dlp` extractors, Deno and `ffmpeg` parse untrusted media in-process / unsandboxed beyond systemd | Medium | Out of scope (upstream) |
| Root updater imports a freshly downloaded yt-dlp as root | Low | Documented |
| Re-download check compares length only | Low | Documented; use keep forever for favourites |
| CSP allows inline style attributes and any https frame | Low | Accepted |
| Wishlist: kids see titles and thumbnails of unfiltered YouTube search results (word list and channel allowlist only) | Medium | Documented; off by default per kid |
| Backups (DB + `config.json` with session secret) unencrypted | Low (0600, root/service user only) | Documented |
| Relocation pointer config with a missing target reopens first-run setup | Low (non-systemd installs only) | Documented |
| Wi-Fi PSK on the boot partition between flashing and the first boot | Low (physical access) | Mitigated; blanked once applied |
| Thumbnail fetch: DNS rebinding between the address check and the connection | Low | Documented |

"Accepted" and "Documented" mean we believe the behaviour is acceptable
once the operator understands it; "Out of scope" items are explicit
non-goals; "Known gap" is unmitigated and open to improvement.

## Reporting issues

See [`SECURITY.md`](../SECURITY.md) for the disclosure process.
