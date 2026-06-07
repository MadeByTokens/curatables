# Curatables Threat Model

> Companion to [`SECURITY.md`](../SECURITY.md). This file describes the
> trust boundaries, the attack surface, and — critically — the risks
> Curatables does **not** mitigate today. It is intentionally honest:
> "we don't defend against X" is a feature of a threat model, not a bug.

## Deployment shape

Curatables is designed for one specific operating environment:

- A single mini-PC running bare-metal Debian/Ubuntu LTS.
- Reachable on a single home LAN, typically as
  `http://curatables.local/` via mDNS.
- One administrator (the parent) with shell access to the box.
- Zero, one, or more "kid" profiles, used by family members on
  trusted client devices on the same LAN.
- **No** internet exposure of the parent dashboard. The egress flow
  is one-way: Curatables fetches from upstream sites (YouTube et al.)
  via `yt-dlp`. The server is not intended to be reverse-proxied
  onto the public internet.

Anything outside this shape (cloud VM, Docker on a multi-tenant box,
public reverse proxy, hostile LAN) is **outside the supported threat
model**. Some of those configurations may still be safe; others
definitely aren't. We'd rather say so than imply blanket safety.

## Trust boundaries and roles

| Role         | Trust level | Capabilities                             |
|--------------|-------------|------------------------------------------|
| Parent       | Trusted     | All routes under `/parent/*`, all kid views, server shell. |
| Kid (profile)| Semi-trusted| Kid UI, kid uploads, kid-owned channels, comments, reactions, search (if enabled). Cannot reach `/parent/*`. |
| Anonymous    | Untrusted   | `/profiles` picker, static assets, `/healthz` (planned). Everything else redirects to `/profiles` or `/parent/login`. |
| LAN attacker | Adversary   | Can reach any HTTP port the box opens. Cannot read disk or process memory. Can sniff plaintext HTTP if the network is unencrypted. |
| WAN attacker | Out of scope| Should not be able to reach the box at all. |

The viewer-resolution rule (`app/dependencies.py:get_viewer`) gives
a selected child profile **priority** over `parent_authenticated`
on kid-facing pages. Parent routes use `require_parent`, which
checks `viewer.is_parent` and ignores `profile_id`. This means a
kid sitting at the parent's logged-in browser cannot reach
`/parent/*` (the parent must log in again from a kid session) —
but a child profile in the session **does** mask the kid UI as
that child until the parent visits `/parent/login` or `/profiles/switch`.

## Authentication model

### Parent password

- Stored in `config.json` as
  `salt_hex:hash_hex`, derived with PBKDF2-HMAC-SHA256, **600,000
  iterations**, 16-byte random salt
  (`app/services/auth.py:hash_password`).
- Verified with `hmac.compare_digest` — timing-safe.
- Set on first run via `/parent/setup`; thereafter `/parent/login`
  is the only entry point.
- **No rate limiting** on `/parent/login` POST today. PBKDF2 at
  600k iterations is the only brake on guessing rate; the
  per-attempt cost on the target hardware is high enough that a
  reasonable password (8+ chars, mixed alphanumeric) is well
  out of reach, but the minimum length enforced at setup is only
  **4 characters**, which is far too short. See *Residual risks*.
- Sessions are signed cookies via Starlette's `SessionMiddleware`,
  using a server-generated secret in `config.json`. `SameSite=Strict`
  is set, defeating most CSRF vectors on its own; the
  `CSRFMiddleware` token check layers on defense-in-depth.

### Child PIN

PINs on child profiles are intentionally a low-friction affordance,
not an access-control boundary. The threat model documents what
they actually do.

- Stored as plaintext on `profiles.pin` (truncated to 10 chars at
  the router; no hashing).
- Compared with `==` in
  `app/features/kid_profiles/router.py:profile_pin` — not
  `hmac.compare_digest`. On a LAN this is not a realistic timing
  channel, but the inconsistency with the parent password path
  is worth noting.
- No rate limiting on `/profiles/pin`. A short numeric PIN does
  not provide meaningful resistance against an automated client
  on the LAN.
- The PIN gate sits on the profile picker, not on individual
  routes. Once a session has `profile_id` set, that profile's
  views render until the session is cleared via
  `/profiles/switch` or `/parent/logout`.
- An empty PIN means "no PIN" — `profile_select` skips the PIN
  page and switches profiles in one POST.

The honest framing: PINs distinguish "which child profile is
this" rather than enforce isolation between siblings. Households
that need stronger isolation should use one device per child
rather than relying on the PIN.

### CSRF

- Per-session token, stored in the signed session cookie, validated
  against the form-submitted `csrf_token` field or the
  `X-CSRF-Token` header (`app/middleware/csrf.py`).
- All `POST/PUT/PATCH/DELETE` requests are checked except the
  paths in `CSRF_EXEMPT_PREFIXES`:
  - `/parent/setup`, `/parent/login`, `/profiles/select`,
    `/profiles/pin` — session-establishment endpoints, no token
    can exist before the session does.
  - `/api/log` — fire-and-forget telemetry beacon from the kid
    watch page, low-value target.
  - `/parent/upload` — vendored `tus-js-client` does not send CSRF
    tokens; relies on parent auth + `SameSite=Strict` for
    protection on the LAN-only, parent-only endpoint.
- Rejection mode: 403 with a plain-text error. No automatic retry.
- The `SameSite=Strict` session cookie defeats nearly all
  cross-origin CSRF on its own; the token middleware is
  belt-and-braces.

## Child-facing attack surface

### Uploads from a child profile

`/upload` accepts XHR multipart uploads from any signed-in child
profile. Mitigations:

- Hard byte ceiling enforced by `BodySizeMiddleware` (configurable;
  defaults are smaller than parent uploads).
- Runtime `ffmpeg`-probed codec allow-list — files with codecs
  outside the list are rejected with an actionable conversion hint.
- Content-hash dedup prevents the same file from filling disk twice.

What the codec allow-list does **not** cover:

- Media-decoder vulnerabilities in `ffmpeg` and the browser's
  media stack. These are upstream issues; Curatables does not
  sandbox `ffmpeg`. Keep the system `ffmpeg` patched.
- File-type confusion at the proxy layer. The kid UI serves
  uploaded files with `Content-Type: video/*`; a misconfigured
  reverse proxy that rewrites that header is out of scope here.
- Sidecar subtitle files (`.vtt`, `.srt`) rendered in the kid
  player. If you customise the subtitle pipeline, verify the
  renderer's escaping behaviour.

### Kid-created channels and comments

- Comments are parent-moderated and rate-limited
  (`app/services/comments.py`). XSS via comment text is mitigated
  by Jinja autoescape; do not disable autoescape in any kid view.
- Channels owned by `owner_profile_id` are visibility-isolated from
  sibling profiles (the queries filter on `owner_profile_id IN
  (NULL, my_profile_id)`). A kid cannot see another kid's
  upload-only channel.
- Channel names, banners, and icons are uploaded by kids and shown
  in the parent dashboard. The parent dashboard runs Jinja
  autoescape on channel names; the icon/banner are served as
  static images with explicit `Content-Type`.

### PIN-gate bypass between sibling profiles

If a parent leaves their browser logged in and a child uses
`/profiles/select`, they can switch into any sibling profile that
has no PIN configured. PIN-protected profiles still require the
PIN. Once switched, the kid UI renders that profile's view.

What this **does not** unlock:

- `/parent/*` — `require_parent` checks `is_parent`, which a child
  profile in the session does not satisfy. The parent must sign
  in again through `/parent/login` (which clears `profile_id`).
- Sibling profiles that have a PIN set.
- Anything off the box — there is no "share with friend" flow.

What it **does** unlock:

- Visibility of a sibling's content, if that sibling has no PIN.
- Watch-history attribution to the wrong profile. Parents reading
  `/parent/stats` will see plays attributed to whichever profile
  was selected at watch time.

## Parent-facing attack surface

### Parent session theft on shared LAN

The session cookie is signed (forgery-resistant) but not encrypted
(value is opaque only because the parent hasn't decoded it). On a
LAN with plaintext HTTP, a passive sniffer can capture the cookie
and replay it. Mitigations:

- `SameSite=Strict` prevents cross-origin reuse — the cookie only
  flows on requests originating from the same site.
- `httponly` is set by `SessionMiddleware` by default, blocking
  document.cookie access from injected JS.
- Session lifetime is bounded by `parent.session_timeout_hours`.

The honest residual risk: **on a hostile or compromised LAN
without TLS, the parent cookie can be captured and replayed.**
Curatables ships HTTP-only by default; HTTPS via local CA is on
the v0.7+ roadmap. Parents on shared networks (apartment-block
Wi-Fi, public hotspots) should not run the parent dashboard
without TLS.

### CSRF on parent routes

Already covered above. The exempt paths are all session-establishment
or non-state-changing telemetry. The one notable exemption is
`/parent/upload` — the rationale is documented in `csrf.py` and
hinges on the SameSite cookie carrying its weight.

### File-handling endpoints

- Data directory relocation (`/parent/settings/move-data`) does
  preflight checks (empty target, free space, no in-flight
  downloads). It's a destructive admin action gated behind parent
  auth; treat the parent password as the only thing standing
  between disk-full and disk-rearranged.
- Backup/restore scripts (`scripts/backup.sh`,
  `scripts/restore.sh`) are intended to run as the curatables
  user, not root. They produce an unencrypted tarball of
  `~/curatables-data/` — including `config.json` (parent password
  hash, session secret) and the SQLite DB (PINs in plaintext).
  Treat backups as parent-secret material.

## Supply-chain risk: `yt-dlp` extractors

`yt-dlp` ships extractors for ~1,800 sites. Each extractor is
arbitrary Python code, fetched in via `pip install yt-dlp`. We
pin the version range in `requirements.txt` but do not
audit extractors.

The relevant exposure: any URL the parent pastes runs through
the matching extractor inside the Curatables process, with the
filesystem permissions of the Curatables user. We do not sandbox
`yt-dlp`. Running Curatables under a dedicated unprivileged user
(the shipped systemd unit does this) is the main mitigation —
it bounds the impact to that user's home directory, including
`config.json` and the SQLite DB.

Operators who plan to expose Curatables beyond their LAN (not
the supported configuration) should be aware that the public
add form would extend that extractor surface to unauthenticated
callers — an additional reason the recommended deployment is
LAN-only.

## Defense-in-depth: what's already in place

- PBKDF2-SHA256 with 600,000 iterations on the parent password.
- `hmac.compare_digest` on parent password verification.
- Per-session CSRF tokens with a small, audited exempt list.
- `SameSite=Strict` session cookies.
- Signed sessions via `itsdangerous`.
- Per-request body size cap (`BodySizeMiddleware`).
- Per-request correlation ID (`RequestIDMiddleware`).
- Runtime ffmpeg codec allow-list on every upload path.
- Rate-limited comment posts.
- Forward-only schema migrator (no `IF NOT EXISTS` drift).
- Disk quota guard refusing new uploads/downloads before disk-full.

## Residual risks and known gaps

| Gap                                  | Severity | Tracking |
|--------------------------------------|----------|----------|
| `/parent/login` not rate-limited     | Medium   | Roadmap  |
| `/profiles/pin` not rate-limited     | Low      | Roadmap  |
| PIN compared with `==`, not `compare_digest` | Low (timing channel not realistic on LAN) | Roadmap |
| PIN stored plaintext in DB           | Low (the DB is already parent-secret) | Documented |
| Minimum parent password length is 4 chars | Medium   | Roadmap  |
| No HTTPS by default                  | Medium (LAN-dependent) | v0.7+ Roadmap |
| `yt-dlp` extractors not sandboxed    | Medium   | Out of scope |
| Backup tarball unencrypted           | Low (treat as parent-secret) | Documented |
| Media-decoder vulnerabilities (ffmpeg, browser stack) | Low (upstream)   | Out of scope |

"Roadmap" entries are accepted as future work; "Out of scope"
items are explicit non-goals at the current product stage.
"Documented" means we believe the current behavior is acceptable
once the operator understands it.

## Reporting issues

See [`SECURITY.md`](../SECURITY.md) for the disclosure process.
