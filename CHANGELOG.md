# Changelog

Shipped feature history by development milestone. The forward-looking
roadmap (v0.6+) lives in [README.md](README.md#roadmap); the design
rationale for each area lives under [docs/](docs/README.md).

Milestones are development phases, not released package versions — there
is no PyPI/Docker release cadence yet (bare-metal Linux is the supported
install, see [docs/deployment.md](docs/deployment.md)).

## Unreleased — Import from a drive

**New for parents**
- Import page: browse a drive, pick movies, episodes or home videos, check
  the guessed titles (from file names, posters, Kodi `.nfo` files), find a
  picture and description on Wikipedia/Wikidata (with the age rating),
  TMDB (optional key), YouTube or a linked page, or pick a frame. Episodes
  of a show can go into one channel, in episode order. Copy, or move off
  the drive once imported.
- Every import shows live progress, conversion speed and time left, and
  can be cancelled at any moment; nothing is left half-made and the file
  on the drive is untouched until its import has finished. Running
  imports show on the dashboard.
- Media on any drive: `setup-external-drive.sh --media-only` keeps the
  database on the SD card and puts the videos on an exFAT, NTFS, FAT32
  or ext4 drive without formatting it.
- Pi image: with an Ethernet cable plugged in, the Pi uses only the cable
  and disconnects Wi-Fi; unplugging the cable brings Wi-Fi back. A Wi-Fi
  network that doesn't show up in a scan is tried once as hidden.
- `curatables.local` works behind routers that drop multicast on Wi-Fi:
  the Pi also answers the lookup directly to the device that asked.
- Pi image, everything from the dashboard: a new **Box** section changes
  the Linux login password and SSH, switches Wi-Fi (testing the new
  network first and going back if it fails), and restarts Curatables,
  the network or the box.
- Two-step first-run setup on the Pi image: after the parent password,
  a different password for the box's Linux login. The image now ships
  with SSH off. Password managers can generate and save both passwords.
- **Box → Drives & backups**: use a USB drive for the videos, move them
  to another drive or back to the SD card, eject, reconnect, check and
  repair, format as exFAT or ext4, open a drive for importing; a power
  warning; back up now, restore, and keep backups on the videos drive.
  FAT32 drives work, with a 4 GB per-file limit.
- If the videos drive is missing, Curatables still starts: kids see a
  calm "videos are resting" screen and the dashboard says how to
  reconnect, while nothing in the library is reset or evicted.
- **Box → Diagnostics**: health checks with what to do for each, buttons
  for the usual fixes, and a downloadable report with passwords removed.
- SD-card recovery files: `curatables-reset.txt` (forgotten parent
  password) and `curatables-diagnose.txt` (a report written to the card),
  next to `curatables-wifi.txt`, explained in `curatables-README.txt`.
- **Box → Updates**: update Curatables from the newest GitHub release
  without re-flashing, keeping the library; it is prepared while the old
  version runs, and the box goes back by itself if the new one does not
  start (or on **Go back**). Update the downloader and all system
  packages from the same page; Debian security updates now install by
  themselves every day.
- **Shut down** button, so the box can be unplugged safely.
- New [parents' guide](docs/parents-guide.md) for the Pi image, written
  for Windows, Mac and phone users.

**Fixes that also help uploads and downloads**
- Conversions had a one-hour limit a Pi 4 can hit on a movie; they now
  run until done, with progress, stall detection and a clean stop.
- Conversions picked streams on their own: cover art could become the
  video, a commentary track the audio, and a subtitle stream could fail
  the whole conversion. They now map one video and one audio stream.
- Uploaded videos could never have subtitles, and `.srt` files were not
  played at all; every subtitle is now served as WebVTT, with language
  names instead of codes.
- Server-side fetches waited out a broken IPv6 route before trying IPv4
  (15 s per picture on some home networks); they now try IPv4 first and
  connect only to the addresses that were checked.
- First boot: Wi-Fi is saved as a NetworkManager profile and retried
  until the network shows up, with a log on the boot partition.

## Unreleased — Wishlist, languages, picture PIN and hardening

**New for kids and parents**
- Kids can ask for new videos (off by default per kid): a Find new page
  searches YouTube through the server under the parent's rules, and the
  parent reviews requests in a Video requests inbox before anything is
  downloaded.
- Kid screens in English, Portuguese (Brazil) and Spanish, per profile.
- Picture PIN: three taps from nine animals, for a child who doesn't
  read numbers yet.
- QR code of the kid address for setting up a tablet.
- Playlist review shows progress and can add just the watched videos;
  Settings has section links and clearer "keep forever" wording.

**Security**
- Content-Security-Policy (no inline scripts left); logout is a POST
  with a CSRF token.
- Thumbnail fetches refuse home-network addresses.
- Failed parent logins are stored, so restarts don't reset the limit,
  and there is a cap across all addresses.
- First-run setup only works during the first 30 minutes after start.
- The Wi-Fi password is removed from the boot partition once applied.
- Deno is installed from a pinned, checksummed release.

**Fixes and maintenance**
- mypy is clean and blocking in CI; the kid visibility rule is one SQL
  fragment; one apt package list for install.sh and the Pi image.
- Deleting a video during its download left its files behind;
  re-uploading a file into another channel silently returned the old
  video (a kid could also skip review that way); firstboot exited early
  for an open network with no psk line; the wheel was missing the
  migrations and schema.

## Unreleased — Safety, Raspberry Pi robustness and a simpler kid screen

**Child safety**
- Channel restriction is an explicit per-profile flag and fails closed:
  deleting a restricted kid's channels no longer unrestricts them; search
  never falls back to the whole library; anonymous visitors see nothing;
  subtitles and channel art check the viewer. One visibility policy
  (`ContentService.visible_channel_ids`) serves every list, search and
  media route.
- Picking a kid profile ends any parent sign-in on that device; changing
  the parent password signs out every other device.
- Kid uploads into shared channels wait for parent approval
  (dashboard: "waiting for your OK").
- Videos re-downloaded after cache eviction go back to review if their
  length changed.
- PIN and password guessing are rate-limited; PINs are stored hashed;
  parent passwords need 8+ characters.
- The root yt-dlp updater touches the data dir only as the service user.
- Fixed stored XSS in parent delete confirmations, CSS injection via
  channel colour, unrestricted image uploads and `file://` thumbnail
  fetches.

**Raspberry Pi + external drive**
- Downloads go through a queue (one at a time by default) with a size
  limit; blocking work moved off the event loop; request-size limits run
  before anything reads the body.
- Cache eviction is least-recently-watched, and frees space when the disk
  is full; thumbnails survive eviction.
- `install.sh --systemd` is a complete permanent install (the shipped
  unit used to run the wrong Python); nightly backups of the database
  and `config.json` work out of the box (they needed a `sqlite3` binary
  that was never installed).
- `scripts/setup-external-drive.sh` moves the library onto a USB drive;
  Curatables refuses to start without the drive instead of opening a
  fresh install, and crash recovery won't reset the library when the
  drive is missing.

**Kid screen**
- Per-child switches for reactions, comments, tags, customizing, uploads
  and automatic subtitles; new profiles start with "watch + react".
- On-screen PIN pad, "Again / Home" card when a video ends, no
  download/cast/picture-in-picture, pictures instead of text for empty
  and error pages, current page marked in the nav, reaction emoji on
  cards, friendly dates, higher contrast, focus ring, reduced-motion
  support and a new "Big & clear" theme.

**Parent dashboard**
- Getting-started checklist, content filters (waiting / downloading /
  failed / hidden), Retry for failed downloads with plain-language
  advice, no more 5-second page reloads, a tidier nav that works on
  phones, working upload progress, and Approve buttons for kid uploads.

**Docs and tooling**
- New: [safety model](docs/safety-model.md), [FAQ](docs/faq.md),
  [configuration reference](docs/configuration.md); install, backup and
  upgrade guides rewritten; design and history docs moved under
  `docs/design/` and `docs/history/`.
- CI lints with ruff and shellcheck and tests Python 3.13.
- Migrations 0004–0007: `profiles.channels_restricted`, hashed PINs,
  `videos.last_played_at`, per-profile feature switches.

## Appliance image & no-terminal updates

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
  verification: [docs/history/ui-and-playback-plan.md](docs/history/ui-and-playback-plan.md).
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
