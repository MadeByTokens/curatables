# Changelog

Shipped feature history by development milestone. The forward-looking
roadmap (v0.6+) lives in [README.md](README.md#roadmap); the design
rationale for each area lives under [docs/](docs/README.md).

Milestones are development phases, not released package versions — there
is no PyPI/Docker release cadence yet (bare-metal Linux is the supported
install, see [docs/deployment.md](docs/deployment.md)).

## Unreleased — Appliance image & no-terminal updates

- **In-app yt-dlp update:** a **Settings → Updates → Update yt-dlp** button
  updates the downloader from the dashboard with no terminal. The sandboxed
  app (`ProtectSystem=strict`, `NoNewPrivileges`) can't pip-install or
  restart itself, so it drops a request flag in the data dir and a new
  root-owned `curatables-updater.path`/`.service` does the pip-upgrade +
  restart, writing a result the dashboard reads back
  (`app/services/updates.py`, `scripts/updater.sh`). yt-dlp only by design.
  Docs: [docs/upgrade.md](docs/upgrade.md).
- **Pre-built Raspberry Pi appliance image:** `pi-gen/build.sh` builds a
  flashable arm64 (Raspberry Pi OS Lite, trixie) image with Curatables, its
  venv, ffmpeg, and Deno pre-installed and all systemd units enabled. No
  baked passwords (OS login set at flash time; parent password set on first
  visit via `/parent/setup`). Networking with no terminal: Ethernet DHCPs
  automatically; Wi-Fi via an editable `curatables-wifi.txt` on the FAT boot
  partition, applied each boot by `curatables-firstboot.service`. Build runs
  in Docker via qemu; a `build-image` GitHub Actions workflow produces the
  `.img.xz` on release. Docs: [pi-gen/README.md](pi-gen/README.md).

## Playback & GUI delivery

- **Playback baseline:** every downloaded and uploaded video is normalized
  on ingest to H.264/AAC, ≤720p30, MP4 `+faststart`, so it plays on old
  devices (Safari/iOS 9, ~2015 tablets) even when the source is VP9/AV1 or
  Opus. The yt-dlp format string prefers `avc1`+`mp4a`; only non-baseline
  pulls pay for a transcode (`app/services/normalize.py`). Design +
  verification: [docs/ui-and-playback-plan.md](docs/ui-and-playback-plan.md).
- **GUI refresh within the iOS-9 baseline:** inline styles consolidated into
  a utility + token CSS layer (single-sourced teal accent), system font
  stack, fixed-aspect thumbnails (no layout shift), ≥44px touch targets,
  zero-JS kid pages except `watch`/`upload`, CSS-grid + responsive parent
  nav behind `@supports`/`@media` (float/iOS-9 fallback intact).
- **Watch page is reload-free:** reactions, logging, subtitle load, tag
  add/remove and comment/reply posting all update in place via tiny ES3
  XHR (a full reload would restart the playing video); each falls back to
  a plain `<form>` POST when JS is off. Fixed an intermittent 403 on the
  kid multipart `/upload` (the CSRF token extractor truncated tokens at a
  `-`).

## v0.5 — Shared Curation

- Export a parent channel as `.ytc` (JSON), plain text, or PDF from
  `/parent/channels/`; import at `/parent/channels/import` (file or paste).
- Imported URLs always flow through the normal review/preview flow and are
  re-fetched from source (file hints ignored to prevent injection); PDF
  export degrades gracefully without `reportlab`.

## v0.4 — Networking, Discovery & Source Coverage

- mDNS / Zeroconf advertisement (`_http._tcp.local.`) → reachable at
  `http://curatables.local/` (`app/services/mdns.py` + systemd unit).
- Multi-source support via yt-dlp (~1,800 sites) with a Tier 1 iframe
  embed allow-list (YouTube, Vimeo, Dailymotion, PeerTube, TED) and a
  composite `{extractor}_{raw_id}` video_id (`video_source.py`,
  `embeds.py`, `ids.py`).

## v0.3 — Storage, Uploads & Caching

- Disk quota guard (configurable minimum free space) + storage report at
  `/parent/storage` with per-channel breakdown and a live free-space chip.
- Parent uploads via resumable tus.io protocol with content-hash dedup and
  an ffmpeg codec allow-list; kid uploads at `/upload` (plain XHR, iOS-9
  compatible) with a smaller ceiling.
- Kid-created channels owned by the creating profile (`owner_profile_id`),
  sibling visibility isolation, parent "(by X)" badge and friendly event
  labels in `/parent/stats`.
- Web file management: bulk video ops on `/parent/content`, file-clean
  deletion (removes on-disk files + thumbnails), channel delete with
  reassignment, parent adoption of kid channels, data-directory relocation.
- Kid library personalization: per-kid title/thumbnail overrides, personal
  tags + tag cloud, kid channel art (banner/icon/color/description),
  per-kid channel bookmarks (one video, many channels, no file copies).
- Parent stats overhaul: summary dashboard (Today / 7 Days / All Time),
  KPI tiles, Top Videos + Per-Kid tables, per-kid and per-video drill-downs,
  per-video moderation.
- Cache lifecycle: background sweep evicts cache-mode files older than
  `cache_days` and re-downloads on demand; per-video "library" toggle pins
  favourites; uploads are never evicted.
- Plumbing: forward-only schema migrator (`app/db/migrator.py` +
  `app/db/migrations/`), CSRF tokens (SameSite=Strict), request-id + body-
  size middleware, timing-safe password compare, rate-limited comments,
  backup/restore scripts + systemd timer ([docs/backup.md](docs/backup.md)).

## v0.2 — Child Profiles & Theming

- Multiple child profiles with PIN selection, display name, per-profile
  settings, and per-profile channel restrictions.
- Themes (base, playful, calm) via CSS custom-property overlays.
- Curated per-profile search, emoji reactions (love/funny/cool/wow/learned/
  boring), threaded parent-moderated family comments.
- Parent channel and profile management (CRUD) with comment moderation.

## v0.1 — MVP

- First-run setup wizard (set parent password).
- Two-step add flow (fetch metadata → preview/edit → confirm), internal
  channels, background downloading with status tracking, subtitle download.
- Kid UI: responsive video grid, player with subtitle tracks, server-
  proxied streaming (kid device never contacts the source).
- Thumbnail caching, usage event logging, duplicate protection, pagination,
  configurable storage path / cache duration / resolution / subtitles.
