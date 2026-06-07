# curatables — Product Requirements Document

> **Status: historical.** This PRD captures the original product
> requirements from project inception and is preserved as a design
> reference. Some intended scope — notably multi-platform host
> support (Mac/Windows/Linux) and the `server/` project layout
> sketched in §9 — did not ship as written. Current capabilities are
> documented in [`README.md`](../README.md); the current architecture
> (single `app/` tree, Linux host target) is in
> [`architecture.md`](architecture.md).

## 1. Problem

YouTube is the largest library of educational and entertaining video content for children, but its interface is designed to maximize engagement, not child safety. YouTube Kids is widely criticized for poor curation and still surfaces inappropriate content. Regular YouTube's parental controls are blunt — either everything or nothing.

Parents want to:
- Choose exactly what their kids can watch
- Avoid ads, algorithmic recommendations, and autoplay rabbit holes
- Monitor what their kids actually watch and search for
- Discover new good content for their kids over time

There is no simple, self-hosted solution that lets a parent curate a private YouTube-like experience for their children.

## 2. Solution

**curatables** is a local server that parents run on their own computer. It provides:

1. A **parent dashboard** (password-protected) for curating content, managing settings, and viewing usage statistics
2. A **kid-facing website** (clean, ad-free, whitelistable URL) where children browse and watch only parent-approved content

The server uses **yt-dlp** as its backend to fetch video metadata and streams from YouTube, with no API keys or YouTube accounts required.

## 3. Users

### Parent (Administrator)
- Runs the server on a home computer (Mac, Windows, or Linux)
- Curates content: approves channels, playlists, and individual videos
- Configures settings: storage, resolution, cache policies, child profiles
- Reviews usage statistics and search logs
- Technical skill level: can download and run an app, but not comfortable with terminals or Docker

### Deployment targets (server)
- Desktop computer (Mac, Windows, Linux)
- Raspberry Pi or other SBC (low-power always-on server)
- Old Android phone via Termux (stretch goal — Termux supports background services)
- Any machine that can run Python and has network access
- Old iOS device on a charger via iSH (stretch goal — requires app in foreground with auto-lock disabled; fragile but functional as a dedicated appliance)

### Client devices (kid-facing UI)
- Any device with a web browser: iPads, iPhones, Android tablets, laptops, desktops
- **Old devices are a key target.** Many parents will repurpose old tablets for kids. The kid UI must work on very old browsers (e.g., Safari on iOS 9, old Android WebView). This means: no modern CSS (no grid, no flexbox gaps, no container queries), no ES6+ JS (no arrow functions, no fetch, no promises), no frameworks. Basic HTML tables/floats, inline styles if needed, XMLHttpRequest. Test on the oldest WebKit/Blink we can find.

### Child (Viewer)
- Accesses the kid-facing website from an iPad, Mac, or any device with a browser
- Browses approved content, watches videos, optionally searches within approved scope
- Age range: 3–14 (UI must work for both young kids and pre-teens)

## 4. Content Model

Content is approved at three levels (most broad to most specific):

| Level | Description | Behavior |
|-------|-------------|----------|
| **Channel** | Approve an entire YouTube channel | All current and future uploads from this channel appear in the kid UI. Parent can exclude specific videos. |
| **Playlist** | Approve a YouTube playlist | All videos in the playlist appear. New additions to the playlist are auto-included. |
| **Video** | Approve a single video | Only that video appears. |

### Content metadata stored per approved item:
- YouTube ID (channel/playlist/video)
- Title, description (parent can override)
- Thumbnail (original, extracted frame, or custom upload)
- Tags/categories (parent-defined, for organizing the kid UI)
- Approval date
- Status: active / hidden / archived

### Search behavior (configurable per child profile):
- **Disabled**: child sees only the curated library (default for young kids)
- **Curated search**: child can search, but results are filtered to approved channels/videos only
- **Open search with logging**: child can search YouTube freely, but all searches are logged and results are not auto-approved — they go to a parent review queue

## 5. Video Storage & Caching

### Storage location
- Parent configures a storage directory (default: `~/curatables-data/`)
- Can be any path: local disk, external drive, NAS mount
- All video files, thumbnails, and metadata live under this directory

### Storage modes (per-video, with a global default):
| Mode | Behavior |
|------|----------|
| **Cache** | Video is downloaded on first watch (or pre-fetched). Auto-deleted after N days (configurable, default: 30). Re-downloaded if watched again. |
| **Library** | Video is downloaded and kept permanently. Never auto-deleted. |

### Resolution presets
Parent selects a default download resolution. Options:
- 360p (low bandwidth, small files)
- 480p (balanced)
- 720p (good quality, recommended default)
- 1080p (high quality, large files)

Can be overridden per-video. The server downloads the best available stream at or below the selected resolution.

### Storage dashboard
- Shows total disk usage, breakdown by cache vs. library
- List of stored videos with size, last watched date, storage mode
- Bulk actions: delete, move to library, change resolution

## 6. Thumbnails

Three options per video, configurable by the parent:

| Option | Description |
|--------|-------------|
| **Original** | Fetched from YouTube (default) |
| **Frame extract** | Parent picks a timestamp; server extracts a frame via ffmpeg |
| **Custom image** | Parent uploads a PNG or JPG |

Thumbnails are stored locally alongside video files. The kid UI never loads images from YouTube directly.

## 7. Kid-Facing UI

### Design principles
- Clean, colorful, distraction-free
- No ads, no comments, no recommended sidebar
- Large touch-friendly cards (works on iPad)
- Fast load times (must work on old devices)
- Responsive: works on phones, tablets, laptops, desktops

### Pages
1. **Home**: grid of video cards (thumbnail + title). Organized by parent-defined categories or "recently added"
2. **Video player**: full-width video player, title, description below. No related videos sidebar. Optional "next video" button within same category/playlist
3. **Search** (if enabled): search bar, results shown as same card grid
4. **Categories/Channels**: browse by category or channel

### Technical constraints
- Bare-bones HTML/CSS/JS — no frameworks, no ES6+, no modern CSS features. Must render on old browsers (Safari iOS 9, old Android WebView). Use HTML tables/floats, XMLHttpRequest, basic CSS.
- Video served through the local server (kid's device never contacts YouTube)
- All assets (thumbnails, scripts, styles) served locally
- Pages must be lightweight — minimal JS, small asset sizes, fast on slow hardware

### Emoji Reactions

Kids can react to videos using emoji buttons below the player (similar to YouTube's like, but more expressive and age-appropriate).

- A row of selectable emojis displayed below the video player (e.g. love, funny, scary, boring, cool, learned something)
- One reaction per child per video (tapping a different emoji replaces the previous one)
- Reactions are logged as events and visible in the parent stats dashboard
- Reactions influence future recommendations (a child who reacts "love" to science videos helps the LLM suggest more science content)
- The parent can configure which emojis are available (per profile or globally)
- Reactions are shown as a count on the video card in the kid home grid (e.g. a small heart count) — makes the library feel alive
- No negative reactions by default (no dislike/thumbs-down unless parent enables it)

### Family Social Network (comments and sharing between profiles)

curatables can behave as a **local family social network**. Siblings and parents can comment on videos, reply to comments, and see each other's reactions — just like YouTube, but within the family and fully supervised.

#### Comments
- Any family member (child or parent) can leave a comment on a video
- Comments support replies (threaded, one level deep — no deep nesting)
- Comments are text-only (no links, no images — keep it simple and safe)
- Parent can delete any comment from the parent dashboard
- Comments are timestamped and attributed to the profile that wrote them

#### Content sharing between profiles

**Channels are the sharing boundary.** Whether siblings see each other's content, comments, and reactions depends on which channels they share.

| Scenario | How it works |
|----------|-------------|
| **Shared channel** | Two profiles are both assigned channel "Science". Both see the same videos, comments, and reactions in that channel. |
| **Private channel** | A channel is assigned to only one profile. Only that child (and the parent) sees its content and comments. |
| **Full separation** | Toddler and teen have zero channels in common. They never see each other's content or comments. |
| **Full sharing** | All profiles are assigned to the same channels. The family shares one library. |

This is configured via the existing `profile_channels` many-to-many table. The parent controls which profiles see which channels — no new mechanism needed.

#### Visibility rules
- **Kids** see comments only on videos in channels they have access to
- **Kids** see reactions and reaction counts only within their accessible channels
- **Parent** sees all comments, reactions, and activity across all profiles
- A child **cannot** see another child's profile name unless they share at least one channel (no "invisible sibling" leaking)

#### Parent moderation
- Parent can delete any comment
- Parent can disable comments globally or per profile
- Parent can disable comments on specific channels or videos
- All comments are visible in the parent stats/activity dashboard

#### Why channels are the right sharing unit
- It maps naturally to how parents already think ("my toddler watches Peppa Pig, my teen watches science videos")
- It's already in the data model (profile_channels table)
- It avoids complex per-video sharing rules
- A parent who wants full sharing just assigns all channels to all profiles
- A parent who wants full separation just doesn't overlap channels

## 8. Parent Dashboard

### Authentication
- Password-protected (set during first-run setup)
- Single parent account (multi-user is a future feature)
- Session-based auth with configurable timeout

### Pages
1. **Content management**: add/remove channels, playlists, videos. Edit metadata, thumbnails, categories. Bulk actions: multi-select checkboxes on the content list with move/hide/unhide/delete via `POST /parent/content/bulk`. File-clean deletion removes on-disk files alongside the database row.
2. **Upload**: upload a local video file (home movies, school plays, downloaded clips) with a resumable transfer via tus.io. Picks a target channel, runs ffprobe validation, rejects unsupported codecs with a clear conversion command, deduplicates by content hash. Kids have their own upload interface at `/upload` (plain XHR multipart for iOS 9 Safari compatibility) with a smaller per-upload ceiling; kids can also create their own channels from there.
3. **Child profiles**: create profiles with different permissions (search enabled/disabled, categories visible, etc.)
4. **Usage statistics**: time watched per day/week, most-watched videos, search terms used, viewing history timeline. Includes friendly labels for `channel_created` and `video_uploaded_by_kid` events so parents see kid activity at a glance.
5. **Storage**: disk usage report (total/free/used), per-channel size breakdown, configurable minimum free-space threshold that gates downloads and uploads before the disk fills. Also supports moving the entire data directory to a new path via `/parent/settings/move-data` with preflight checks.
6. **Settings**: server port, storage path, cache duration, default resolution, parent password change, advanced toggles (anti-bot, session, min free disk, max upload size, max kid upload size, data directory relocation)
7. **Channels**: create/rename/delete channels, including delete-with-reassignment that moves videos to another channel instead of orphaning them. Parent can adopt kid-owned channels (flip owner to NULL) or reassign them to a different kid profile via the channel edit form. Kid-owned channels show a "(by X)" badge in the parent channels list.
8. **Search & discovery**: parent can search YouTube from the dashboard, preview videos, and approve them directly

## 9. Usage Logging

All kid interactions are logged for parent review and future LLM integration.

### Events logged:
| Event | Fields |
|-------|--------|
| `video_play` | child_id, video_id, timestamp |
| `video_pause` | child_id, video_id, timestamp, position_seconds |
| `video_complete` | child_id, video_id, timestamp, total_watch_seconds |
| `video_seek` | child_id, video_id, timestamp, from_position, to_position |
| `search` | child_id, query, timestamp, result_count |
| `page_view` | child_id, page, timestamp |
| `video_reaction` | child_id, video_id, timestamp, emoji |
| `comment_post` | child_id, video_id, timestamp, comment_id |
| `comment_reply` | child_id, video_id, timestamp, comment_id, parent_comment_id |

### Storage format
- SQLite database (single file, zero config, easy backup)
- Append-only event log table
- Summary/aggregate tables updated periodically for fast dashboard queries

### LLM-readiness
- Events are structured and timestamped — trivially exportable as JSON/CSV
- Future: export a child's viewing history + search patterns as context for an LLM to suggest new content matching parent preferences
- Future: LLM analyzes viewing patterns to surface recommendations in the parent dashboard

## 10. Dependencies

### Runtime dependencies
| Dependency | Why | Size | Notes |
|-----------|-----|------|-------|
| **Python 3.10+** | Our server runtime, yt-dlp's runtime | ~30 MB | Must be bundled for non-technical users |
| **yt-dlp** | YouTube video/metadata extraction | ~15 MB (pip) | Pure Python, installed via pip |
| **ffmpeg** | Merge video+audio streams, thumbnail extraction | ~80-150 MB | Platform-specific native binary, not pip-installable |
| **Deno** | JS runtime required by yt-dlp for YouTube | ~40 MB | **Effectively mandatory** — without it, YouTube blocks yt-dlp. Alternatives: Node.js, QuickJS, Bun. |

Total footprint: ~170-240 MB before any video storage.

### Why this matters for distribution
Four separate dependencies is too many for a non-technical parent to install manually. All packaging/distribution methods must bundle everything into a single download or single setup step. The server should check for all dependencies on startup and provide clear, actionable error messages if anything is missing.

### Distribution strategy

| Method | Target audience | What's bundled | Status |
|--------|----------------|----------------|--------|
| **`pip install curatables`** | Developers, tinkerers | yt-dlp only (user installs Python, ffmpeg, Deno) | MVP |
| **One-click installer** (.dmg, .exe, .deb) | Normal parents on desktop | Python + yt-dlp + ffmpeg + Deno, all bundled | Post-MVP |
| **Pre-built RPi image** (.img) | Parents who buy a Pi for this | Complete OS + everything pre-configured. Flash SD card, plug in, done. | Post-MVP |
| **Portable app / USB stick** | Parents who want no install | Self-contained directory, runs from anywhere | Post-MVP |
| **NAS package** (Synology, QNAP) | Families with a NAS | Docker container or native SPK/QPKG | Future |
| **Termux setup script** | Old Android phone as server | Automated script installs everything in Termux | Future |

### Stack
| Component | Technology | Rationale |
|-----------|------------|-----------|
| Backend | Python + FastAPI | Async-friendly, yt-dlp is native Python, fast development |
| Database | SQLite | Zero config, single file, easy backup, sufficient for single-family use |
| Kid frontend | HTML + CSS + vanilla JS | Must load on old iPads, no build step needed |
| Parent dashboard | HTML + HTMX (or server-rendered templates) | Keep simple, avoid SPA complexity |
| Video player | Native `<video>` element + hls.js fallback | Broad device support |
| Video backend | yt-dlp (Python library) | No API keys, handles YouTube anti-bot, actively maintained |
| JS runtime | Deno (or Node.js) | Required by yt-dlp for YouTube extraction |
| Thumbnail extraction | ffmpeg (via subprocess) | Already required for video stream merging |

### Project structure (planned)
```
curatables/
  curatables.py          # existing CLI tool (kept as standalone utility)
  server/
    app.py              # FastAPI application entry point
    config.py           # configuration management
    db.py               # SQLite database setup and migrations
    models.py           # data models
    routes/
      parent.py         # parent dashboard API routes
      kid.py            # kid-facing API routes
      media.py          # video/thumbnail streaming routes
    services/
      youtube.py        # yt-dlp wrapper (search, fetch, download)
      storage.py        # video file management, caching, cleanup
      thumbnails.py     # thumbnail management
      logging.py        # usage event logging
    static/
      kid/              # kid-facing UI assets
      parent/           # parent dashboard assets
    templates/
      kid/              # kid-facing HTML templates
      parent/           # parent dashboard HTML templates
```

### Video streaming flow
1. Kid clicks a video card
2. Kid frontend requests `/play/<video_id>` from the server
3. Server checks the video is approved for this child profile
4. If video is cached locally: serve from disk
5. If not cached: fetch stream URL via yt-dlp, start downloading, and begin streaming as data arrives
6. Log the `video_play` event
7. Kid frontend plays the video via `<video>` tag pointing at the server's stream endpoint

### Data directory structure
```
~/curatables-data/              # configurable root
  config.json                    # server configuration
  db/
    curatables.db                # SQLite database
  videos/                        # yt-dlp-sourced videos
    <video_id>/                  # 11-char YouTube ID
      video.mp4                  # downloaded video file
      thumb.jpg                  # thumbnail
      meta.json                  # cached metadata
  uploads/                       # parent-uploaded originals (no transcoding)
    <video_id>/                  # up_<sha256[:16]>
      video.<ext>                # original container (mp4/mkv/webm/mov/...)
    .tmp/                        # in-progress resumable uploads, swept at startup
      <token>                    # tus upload bytes
      <token>.json               # tus upload sidecar (filename, size, channel, title)
  thumbnails/
    custom/
      <video_id>.png             # custom uploaded thumbnails
  logs/                          # server access and error logs
```

## 11. Configuration

All configuration stored in a single YAML or JSON file (`config.yaml` / `config.json`) inside the data directory.

### Key settings:
| Setting | Default | Description |
|---------|---------|-------------|
| `server.port` | 8080 | HTTP port for both parent and kid UIs |
| `server.host` | 0.0.0.0 | Listen address |
| `storage.path` | ~/curatables-data | Root data directory |
| `storage.default_mode` | cache | Default storage mode (cache or library) |
| `storage.cache_days` | 30 | Days before cached videos are deleted |
| `storage.default_resolution` | 720p | Default download resolution |
| `storage.min_free_disk_bytes` | 2 GB | Refuse new downloads and uploads when free space would drop below this |
| `storage.max_upload_bytes` | 10 GB | Absolute upper bound for a single uploaded video file |
| `storage.max_kid_upload_bytes` | 500 MB | Per-upload ceiling for kid profiles, applied on top of the system-wide quota |
| `parent.password_hash` | (set at first run) | Bcrypt hash of parent password |
| `parent.session_timeout` | 24h | Dashboard session duration |

## 12. Networking & Access

### HTTP vs HTTPS

- **MVP: HTTP only.** Chrome and Safari allow HTTP to local/private IPs. All features we need (`<video>` playback, forms, cookies, basic JS) work over HTTP. The "Not Secure" address bar badge is cosmetic — nothing is blocked.
- **v0.2: Optional local CA.** First-run wizard generates a local Certificate Authority. Parent dashboard includes a "Secure My Devices" page with per-platform instructions and a QR code to download/install the CA cert on each device. One-time setup per device.
- **Future: Tailscale integration** as an optional add-on for parents who want remote access with real HTTPS.

### mDNS / Local DNS

Typing an IP address is hard for kids and error-prone. The server should advertise itself via **mDNS** (Bonjour/Avahi) so devices on the local network can reach it at a friendly name like `curatables.local`.

- mDNS works well on macOS and iOS (Bonjour is built in)
- Works on most Linux distributions (via Avahi)
- Flaky on Windows and some Android devices — fallback to IP is always available
- The parent dashboard should show the current access URL (mDNS name or IP) and offer a QR code that kids can scan to bookmark it
- If mDNS is unavailable, the setup wizard helps the parent assign a static IP or configure their router's local DNS

### Access URLs

| Interface | URL example | Auth |
|-----------|-------------|------|
| Kid UI | `http://curatables.local/` | None (shows only approved content) |
| Parent dashboard | `http://curatables.local/parent/` | Password required |
| API | `http://curatables.local/api/` | Session token |

## 13. Security Considerations

- Parent dashboard is password-protected; kid UI is not (by design — it only shows approved content)
- The server binds to the local network only — not exposed to the internet
- No data leaves the home network (all YouTube fetching happens server-side)
- Parent password is stored as a PBKDF2-SHA256 hash, never in plaintext
- Kid UI has no way to access parent dashboard routes (separate route prefixes, session checks)
- Rate limiting on parent login to prevent brute force

### Crash recovery & data integrity

The server must survive unclean shutdowns (RPi power loss, laptop lid close, killed process) without data corruption or requiring manual intervention.

- **SQLite WAL mode**: crash-safe writes, automatic rollback of incomplete transactions on restart
- **Atomic config writes**: config.json written to a temp file, fsynced, then atomically renamed — no partial config on crash
- **Startup recovery**: on every boot, the server automatically resets interrupted downloads, cleans up partial files, and verifies that "ready" videos still have files on disk
- **Design rule**: no crash scenario should leave the system in a state that requires manual intervention to fix

### Backend independence

All YouTube interaction (metadata, downloads) goes through an abstract `VideoBackend` interface. The only implementation today is yt-dlp. If yt-dlp is abandoned or blocked, a new backend can be created by implementing one class and changing one line of configuration — no service, route, or template code needs to change.

### YouTube anti-bot strategy

YouTube blocks automated requests. curatables uses a layered approach:

1. **Browser impersonation** (default, recommended) — uses curl_cffi to make yt-dlp's TLS fingerprint look like a real browser (Chrome, Firefox, Safari). No login or cookies needed. Works on headless servers (RPi). Enabled by default.

2. **Cookie file** (optional fallback) — Netscape-format cookies.txt exported from a browser. Useful if impersonation alone isn't enough for certain content.

3. **Browser cookies** (desktop fallback) — reads cookies directly from a browser installed on the same machine. Only works on desktops.

4. **OAuth device flow** (future) — the parent sees a code in the dashboard, opens a Google URL on their phone, enters the code, and authorizes. A token is stored locally on the server. No username or password ever touches our system. Similar to how Claude Code authenticates. This is the ideal solution for authenticated access without privacy risk.

**Security rule:** curatables NEVER stores, transmits, or asks for Google usernames or passwords. OAuth device flow tokens are stored locally and can be revoked by the parent at any time from their Google account.

## 14. Shared Curation ("Community Channels")

Parents can export their curated content as a **shared channel** that other parents can import into their own curatables server. A shared channel appears alongside the parent's own curated content and can be accepted as-is or cherry-picked.

### Sharing format

A shared channel is a portable JSON document (`.ytc` extension) containing:
- Channel name, description, author (optional)
- List of approved YouTube video/channel/playlist IDs with metadata
- Categories/tags
- Version timestamp

This is the canonical format. All sharing methods produce or link to this same format.

### Design constraint

The server runs locally (home computer, Raspberry Pi, old phone). There is no public URL. Parents share curated channels the same way they share any other file — via chat apps, email, cloud storage, or in person. The export format must **preview well on any device** without special software.

### Export formats

| Format | Description | Best for |
|--------|-------------|----------|
| **PDF** (primary) | A nicely formatted document with channel name, video titles, categories, and thumbnails. Machine-readable data (video IDs, metadata) is embedded as a QR code on the last page and as invisible text in the PDF. Previews inline in WhatsApp, iMessage, Telegram, email, Google Drive, iCloud — no app needed to view it. **This is the default sharing format.** | Everyone — chat apps, email, cloud links, AirDrop |
| **Image (PNG)** | A shareable card/poster with channel name, video titles, and a QR code containing the data. Easy to screenshot and forward. Limited to smaller collections (~30 videos). | Quick shares, social media posts, printed handouts |
| **Plain text** | A copy-pasteable list of YouTube URLs + titles. Works in any chat. Any curatables server can parse it. | Universal fallback, works outside curatables |
| **`.ytc` file** (JSON) | Raw machine-readable format for technical users. Can be hosted on GitHub Gist/repo for versioned, subscribable community channels. | Technical parents, community curation projects |

### Import methods

All import paths converge to the same flow: **preview contents, select what to add, confirm**.

| Method | How it works |
|--------|-------------|
| **Upload file** | Drag-and-drop or file-pick a PDF, PNG, or `.ytc` file into the parent dashboard import page. Server extracts the embedded data automatically. |
| **Scan QR code** | Point device camera at the QR code (from a PDF, PNG, or printed handout). Data is decoded and fed into the import flow. |
| **Paste text** | Paste a plain-text list of YouTube URLs into the import box |
| **Paste URL** | Paste a link to a hosted `.ytc` file (GitHub Gist, Google Drive, etc.) |

### Subscribing to shared channels (future)

For `.ytc` files hosted at a stable URL (e.g., GitHub Gist/repo), a parent can **subscribe**. The server periodically checks for updates and notifies the parent of new additions. The parent still approves each new video before it appears in the kid UI — no auto-adding. This feature requires the channel to be hosted somewhere persistent and is aimed at community curation projects.

### Trust & safety

- Importing a shared channel never auto-approves content for kids. The parent always reviews and confirms.
- Shared channels contain YouTube IDs and metadata only — no video files, no executable content.
- Parents can flag or block shared channels they find inappropriate.

## 15. MVP Scope (v0.1) — IMPLEMENTED

The following is working as of v0.1:

1. **Server**: FastAPI app with SQLite, configurable storage path, three-layer architecture (routes → services → repositories)
2. **First-run setup**: set parent password via setup wizard
3. **Parent dashboard**: login/logout, dashboard with stats, add content by URL (video/channel/playlist), two-step add flow (preview/edit metadata before confirming), edit title/description/resolution/channel after adding, hide/activate/delete videos, responsive card layout
4. **Internal channels**: parent-defined categories to organize content for the kid UI
5. **Kid UI**: responsive video grid with channel navigation, pagination, video player page with subtitle tracks, works on mobile
6. **Video downloading**: background download on add (not on first watch), download status tracking (pending/downloading/ready/error), duplicate protection, configurable resolution (360p–1080p)
7. **Subtitles**: automatic download of subtitles via yt-dlp, configurable languages, served as VTT tracks in the kid player
8. **Thumbnails**: original from YouTube, downloaded and cached when content is added
9. **Usage logging**: video play/complete events, time watched today stat, recent activity list
10. **Settings**: storage path, cache duration (0 = keep forever), default resolution, subtitle languages, password change
11. **Schema**: designed for full PRD scope — profiles, profile-channel permissions, and all future fields are in the schema from day one
12. **Installation**: `python run.py` with dependency checking
13. **Crash recovery**: atomic config writes, startup recovery for interrupted downloads, SQLite WAL mode
14. **Backend abstraction**: yt-dlp isolated behind VideoBackend interface — replaceable without touching services or routes

### v0.2 — IMPLEMENTED:
- Child profiles with PIN, avatar, display name, theme, search mode, channel restrictions
- Profile picker for kids (auto-select, PIN entry, switch)
- Per-profile display name in kid UI header
- Theme support (base, playful, calm) via CSS custom property overlays with old-browser fallbacks
- Curated search within approved content (per-profile toggle)
- Emoji reactions on videos (love, funny, cool, wow, learned, boring) with event logging
- Family comments (threaded 1-level, channel-scoped visibility, parent-moderated)
- Content sharing between profiles via shared channels
- Parent channel management CRUD
- Parent profile management CRUD with channel assignments
- Comment moderation in parent stats dashboard

### v0.3 — Storage, Uploads & File Management (uploads line complete):
- **Phase 1 (DONE)**: Disk quota guard with configurable minimum free space. New `/parent/storage` report page with total/free/used cards and per-channel size breakdown. Live free-space chip in the parent topnav on every page.
- **Phase 2 (DONE)**: Parent video uploads via a resumable tus.io 1.0.0 protocol implemented directly in FastAPI. Content-hash video IDs (`up_<sha256[:16]>`) for free dedup. Runtime ffmpeg decoder allow-list via a new `MediaProbeService` that tells users the exact conversion command when a codec is not supported. Uploaded files live in a separate `uploads/` tree; the existing `videos.storage_mode` column discriminates without a schema migration. Auto-created "Family" channel as default upload target. Maximum upload size configurable in advanced settings (default 10 GB).
- **Phase 3 (DONE)**: Kid-side uploads at `/upload` using plain XHR multipart (iOS 9 Safari compatible, no tus-js-client dependency on the kid side). Kid-created channels via a new `owner_profile_id` column on `channels` with an idempotent ALTER TABLE migration. Sibling visibility isolation — a kid-owned channel is only visible to the creating kid until the parent explicitly shares it. Two new event types (`channel_created`, `video_uploaded_by_kid`) surface as friendly labels on the parent stats page. New `max_kid_upload_bytes` setting (default 500 MB). `require_child` dependency for kid-only routes.
- **Phase 4 (DONE)**: Web-based file management — bulk video operations at `/parent/content/bulk` (move/hide/unhide/delete via HTML5 form-association checkboxes), file-clean video deletion that removes on-disk files and thumbnails alongside the database row, channel delete with optional reassignment, parent adoption/reassignment of kid-owned channels via the channel edit form, data directory relocation via `/parent/settings/move-data` with preflight checks (empty target, enough free space, no in-flight downloads). Fixed a latent bug where `recover_from_crash` would reset uploaded videos to pending on every restart.
- Library mode (per-video toggle, keep forever vs cache)
- Cache auto-cleanup for expired videos
- Custom thumbnails (frame extract, upload)
- OAuth device flow for YouTube authentication (code on screen → authorize on phone → token stored locally, no passwords)

### Deferred to v0.4:
- mDNS / local DNS (curatables.local)
- QR code for kid device setup
- Optional HTTPS via local CA

### Deferred to v0.5:
- Shared curation (export/import curated channels as PDF/PNG/text/.ytc)
- Subscribe to hosted channel files

### Deferred to v0.6+:
- LLM-powered recommendations
- Desktop app packaging (.dmg, .exe)
- Pre-built RPi image, NAS packages, Termux script
- Playlist auto-sync (check for new uploads)
- Open search mode with parent review queue
