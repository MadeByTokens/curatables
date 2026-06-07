# Security Policy

Curatables is a self-hosted family appliance: a single Linux box on a
home LAN, one parent, one or more kid profiles. The threat surface
reflects that — see [`docs/threat-model.md`](docs/threat-model.md) for
the full model, including residual risks the codebase does **not**
mitigate today.

## Supported versions

Curatables is pre-1.0. Only the **latest tagged release** and the
**`main` branch** receive security fixes. There are no LTS branches
or backports — if you're on an older tag, the fix is "upgrade."

| Version       | Status                          |
|---------------|---------------------------------|
| `main`        | Supported (active development)  |
| Latest tag    | Supported                       |
| Older tags    | Not supported — upgrade         |

## Reporting a vulnerability

**Do not open a public GitHub issue for security reports.** Public
disclosure before a fix is ready puts every running install at risk.

Email the maintainer at the address listed on the
[`LICENSE`](LICENSE) file (`ricardo.azambuja@gmail.com`). PGP is
not currently offered; if you need an encrypted channel, say so in
your first message and we'll set one up.

Please include:

- A description of the issue and its impact (what an attacker can do).
- A minimal reproduction (URL, request body, sequence of clicks —
  whatever is needed to trigger it).
- The Curatables commit hash or tag you tested against.
- Your preferred name/handle for the eventual credit line, or "anonymous."

### What to expect

| Step                       | Target                          |
|----------------------------|---------------------------------|
| Acknowledgement of report  | within **3 business days**      |
| Initial triage + severity  | within **7 days**               |
| Fix in `main`              | depends on severity (see below) |
| Public disclosure          | coordinated with reporter       |

Severity-driven fix targets (best effort — Curatables is a
side-project, not a funded vendor):

- **Critical** (RCE, auth bypass on parent surface, kid data leak
  off-LAN): patch within **14 days**.
- **High** (privilege escalation between parent/kid, persistent
  XSS in parent dashboard): patch within **30 days**.
- **Medium / Low** (information disclosure on LAN, denial of
  service, hardening gaps): patch in the next release.

## Scope

**In scope:**

- The Curatables server code under `app/`.
- Shipped scripts (`scripts/install.sh`, `scripts/backup.sh`,
  `scripts/restore.sh`, `run.py`, `curatables-cli.py`).
- The shipped systemd unit (`systemd/curatables.service`).
- Vendored client-side code (`app/static/`, including
  `tus-js-client`).
- Documentation that misleads operators into an insecure config.

**Out of scope:**

- `yt-dlp` itself and its ~1,800 site extractors. Curatables
  invokes `yt-dlp` as a library; vulnerabilities in extractors
  are upstream issues. We do, however, want to know about
  Curatables-specific ways an attacker could reach an unsafe
  extractor (e.g. via the public add form on a server exposed
  beyond the LAN).
- `ffmpeg` and other system binaries. Same upstream principle.
- Browser-specific bugs in the kid UI's iOS-9-Safari fallback
  paths, unless Curatables itself is the vector.
- Issues that require physical access to the server box (LAN
  access is in scope; physical access is not).
- Issues that require a malicious browser extension on the
  parent's device.

## Hall of fame

We'll list reporters here once the first fix lands. Send your
preferred credit line in your initial report, or say "anonymous"
to opt out.
