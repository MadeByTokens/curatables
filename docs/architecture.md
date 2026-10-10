# curatables — Architecture

This document describes the software architecture of curatables. Every contributor
should read this before writing code. It explains **why** things are where they are,
not just where they are.

For operational concerns (running Curatables as a long-lived service,
binding port 80, and the mDNS/`.local` discovery story) see
[`deployment.md`](deployment.md). For the canonical list of every
package, binary, filesystem path, port and capability Curatables
needs, see [`dependencies.md`](dependencies.md).

---

## Design Principles

1. **Feature isolation.** Each feature lives in its own directory. A pull request
   that adds "playlist support" should touch `app/features/playlists/` and one
   line in `app/main.py`. Nothing else.

2. **Three-layer separation.** Routes parse HTTP. Services hold business logic.
   Repositories own database queries. No layer may skip another — a route never
   executes SQL, a repository never raises an HTTP error.

3. **One visibility policy.** Every request carries a `ViewerContext` that
   says who is looking (parent, child profile, anonymous). What that viewer
   may see is decided in one place, `ContentService.visible_channel_ids`
   (see [Kid visibility](#kid-visibility)). Features ask it instead of
   reimplementing access checks.

4. **Fail closed.** When the rules are ambiguous (an empty whitelist, an
   anonymous visitor, a missing drive), the kid sees less, not more.

5. **Old-device-first frontend.** The kid-facing UI must work on Safari iOS 9
   and old Android WebView. No ES6+, no CSS grid, no frameworks. The parent
   dashboard has no such constraint but stays server-rendered for simplicity.

6. **Small-box friendly.** The reference host is a Raspberry Pi 4B with the
   library on a USB drive. Heavy work (yt-dlp, ffmpeg) is queued, never run
   on the event loop, and never fanned out without a limit.

7. **No magic.** Explicit imports, explicit wiring, no classpath scanning, no
   decorators that hide control flow. A new contributor should be able to trace
   a request from URL to database in under two minutes.

---

## Project Layout

Directory-level only; open the directory for the current file list.

| Path | What lives there |
|---|---|
| `app/main.py` | `create_app()`: config, logging, middleware, Jinja setup, exception handlers, router wiring. |
| `app/lifecycle.py` | Startup work and background tasks (see [Startup and background work](#startup-and-background-work)). |
| `app/config.py` | `Config` dataclasses, loaded from and atomically saved to `<data dir>/config.json`. |
| `app/dependencies.py` | Every FastAPI `Depends()` provider: DB connection, repositories, services, `get_viewer`, `require_parent`, `require_child`, `require_kid_feature`. The only place the layers are wired together. |
| `app/web.py`, `app/template_utils.py` | Small shared request helpers (`is_xhr`) and template helpers (kid rendering, Jinja filters, asset fingerprint). |
| `app/middleware/` | ASGI middleware: host check, body size limit, CSRF, request ID, Prometheus. |
| `app/db/` | Connection setup, forward-only migrator, `migrations/`, the `schema.sql` reference snapshot, and `recover_from_crash`. |
| `app/models/` | Plain dataclasses (`Video`, `Channel`, `Profile`, `ViewerContext`, ...). No logic, no imports from other layers. |
| `app/repositories/` | SQL, and only SQL. One repository per table or small cluster of tables. |
| `app/services/` | Business logic. No HTTP, no SQL, no direct yt-dlp. |
| `app/backends/` | `VideoBackend` interface and the yt-dlp implementation. |
| `app/features/<name>/router.py` | HTTP layer, one directory per feature (`parent_*`, `kid_*`, `media`, `api`, `healthz`). `_template/` is the skeleton to copy. |
| `app/templates/base/` | Jinja templates: `kid/` and `parent/`. Files starting with `_` are partials. |
| `app/static/` | CSS/JS: `kid/` (old-browser-safe), `parent/`, `js/` (vendored tus client). |
| `tests/` | Flat: one `test_*.py` per area, `conftest.py` fixtures, `fixtures/` (incl. a fake yt-dlp for the smoke suite). |
| `syshelper/` | The root system helper for the Pi image: standard library only, one module per area (accounts, Wi-Fi, drives, backups, diagnostics, updates). See [System helper](#system-helper). |
| `scripts/` | `install.sh`, `apply-system.sh` (installs units and system files; used by the image build and by updates), backup/restore, the root-side `updater.sh`, drive setup, `curatables-helper.py`, Pi `firstboot.sh`, `nm-wired-only.sh`, `mdns-unicast.py`, `seed_demo.py`. |
| `systemd/` | Units for the server, the system helper, the yt-dlp updater (path + service), the nightly backup (service + timer), and the Pi's first boot, SD-card recovery, SD-card diagnostics and unicast mDNS. |
| `pi-gen/` | Raspberry Pi appliance image build ([`pi-gen/README.md`](../pi-gen/README.md)). |
| `tools/` | `curatables-cli.py`, a standalone yt-dlp helper; not part of the server. |
| `docs/` | User and contributor docs; `docs/design/` holds designs for unbuilt features, `docs/history/` superseded plans and research. |

---

## Layers in Detail

### 1. Models (`app/models/`)

Plain Python dataclasses, the shared vocabulary of the application. The
one with the most reach is `ViewerContext`:

```python
@dataclass
class ViewerContext:
    viewer_type: str                            # "parent" | "child" | "anonymous"
    profile_id: int | None = None
    allowed_channel_ids: list[int] | None = None  # None = unrestricted profile
    search_mode: str = "disabled"
    theme: str = "base"
    allow_comments: bool = True                 # per-profile feature switches
    allow_reactions: bool = True
    allow_tags: bool = True
    allow_customize: bool = True
    allow_upload: bool = True
    auto_subtitles: bool = True
    # ...
```

### 2. Repositories (`app/repositories/`)

Each repository owns the SQL for one table (or a small cluster of related
tables). Repositories accept a `sqlite3.Connection` and return model objects.

Rules:
- No business logic. A repository does not decide **whether** a video should
  be deleted — it just deletes.
- No HTTP concepts. No `Request`, no `Response`, no status codes.
- SQL is parameterized, never string-formatted.

### 3. Services (`app/services/`)

Services contain business logic. They call repositories (never raw SQL) and
other services. They raise domain exceptions (e.g. `VideoNotFound`), never
HTTP exceptions. Services worth knowing about:

- **`ContentService`** (`content.py`) — add/list/search/moderate videos, the
  visibility policy, download orchestration, rehydrating evicted videos.
- **`StorageService`** (`storage.py`) — file layout, the free-space guard,
  downloads through the backend, cache eviction.
- **`DownloadQueue`** (`download_queue.py`) — the shared download worker
  pool (see [Downloads](#downloads)).
- **`MediaNormalizer`** (`normalize.py`) + **`MediaProbeService`** — the
  ingest-time playback baseline (see [Playback baseline](#playback-baseline)).
- **`ThumbnailService`**, **`ChannelService`** (incl. kid channel art),
  **`UploadService`** (tus create/append/finalize), **`RelocationService`**.
- **`ReactionService`**, **`CommentService`**, **`KidLibraryService`**
  (per-kid title/thumbnail overrides, tags, channel bookmarks),
  **`StatsService`**.
- **`AuthService`** (PBKDF2 password + PIN hashing, parent session start/check),
  **`CSRFService`**, **`RateLimiter`**, **`MetricsService`**.
- **`updates.py`** — app-side half of the yt-dlp updater (see
  [In-app yt-dlp update](#in-app-yt-dlp-update)).

### 4. Features / Routes (`app/features/`)

Each feature directory has a `router.py` that defines an `APIRouter`. Routes
are thin — they parse the request, call a service, and return a response.

```python
# app/features/kid_watch/router.py (abridged)
@router.get("/watch/{video_id}", response_class=HTMLResponse)
def watch(request: Request, video_id: str,
          viewer: ViewerContext = Depends(get_viewer),
          content: ContentService = Depends(get_content_service)):
    try:
        video = content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        ...
    return render_kid_template(request, viewer, "kid/watch.html", {"video": video})
```

Rules:
- A route calls services, never a repository directly.
- A route never contains `if viewer.viewer_type == ...` access logic — that
  belongs in the service or a `require_*` dependency.
- Blocking work (transcodes, yt-dlp metadata fetches for imports) runs in
  the threadpool (a sync `def` route or `run_in_threadpool`), never directly
  in an `async def` handler.

### 5. Dependencies (`app/dependencies.py`)

FastAPI's `Depends()` wires everything together. `get_db` opens one
connection per request; every service has a `get_*_service` provider.
Access is expressed as dependencies:

- `get_viewer` — builds the `ViewerContext`. A selected kid profile wins
  on kid pages even if the parent is also logged in on that browser.
- `require_parent` — raises `NotAuthenticated` → redirect to `/parent/login`.
- `require_child` — raises `NotAChild` → redirect to `/profiles`.
- `require_kid_feature("allow_comments", allow_parent=False)` — a kid whose
  profile has that switch on. A switched-off feature raises
  `FeatureDisabled`: GET redirects home, other methods get a 403. The
  templates hide the same features, so the server check is the backstop.

For testing: `app.dependency_overrides[get_db] = ...` (see `tests/conftest.py`).

---

## Kid visibility

`ContentService.visible_channel_ids(viewer)` is the single policy:

- **parent** → `None` (everything, including videos without a channel);
- **kid** → the parent-created channels their profile allows, plus the
  channels the kid owns; a sibling's channel only if it is explicitly
  whitelisted;
- **anyone else** → `[]` (nothing).

Restriction is an explicit flag, `profiles.channels_restricted`. When it is
1 the kid sees only the channels listed in `profile_channels` (plus owned
ones), so an empty list means *nothing*. When it is 0, `profile_channels`
is ignored and every parent-created channel is visible.

`can_view(video, viewer)` and `channels_for_viewer(viewer)` derive from it,
and every list/search/get/rehydrate path, the media routes (`/media/video`,
`/media/subs`, `/media/channel/*`), `/api/log`, `/api/react` and kid comments
go through it. A video with no channel is visible to the parent only.
Comments follow the same rule: a kid sees comments by family members who
can see that video's channel. The rule is written once, as the SQL
condition `kid_sees_channel()` in `app/repositories/channel_repo.py`;
`list_visible_to` and the comment queries both use it.

Two more states keep content hidden until a parent acts: `videos.status =
'review'` (a kid upload into a shared channel, or a re-download that no
longer matches what was approved) and `'hidden'`.

---

## Database Schema

Schema changes are managed by a forward-only migrator (`app/db/migrator.py`).
Each change is a numbered file under `app/db/migrations/`: either `.sql`
applied via `executescript`, or `.py` with `def up(conn): ...` for logic
SQLite's DDL can't express (e.g. conditional `ALTER TABLE ADD COLUMN`).
Applied versions are recorded in `schema_migrations`; each migration runs
in its own transaction. `app/db/schema.sql` is a **reference snapshot** of
the current shape and is never executed.

**Adding a column or table:** write `app/db/migrations/NNNN_slug.sql` (or
`.py`), update `schema.sql` to match. There is no downgrade path, so
migrations must be idempotent and safe on existing data.

| Migration | Change |
|---|---|
| `0001_initial.sql` | Base schema. |
| `0002_bootstrap_existing.py` | Adds columns a pre-migrator database may lack. |
| `0003_keep_forever_and_evicted.py` | `videos.keep_forever`; the `'evicted'` download status. |
| `0004_profile_channels_restricted.py` | `profiles.channels_restricted`, backfilled from existing `profile_channels` rows. |
| `0005_hash_profile_pins.py` | Rewrites plaintext kid PINs as PBKDF2 hashes. |
| `0006_videos_last_played_at.py` | `videos.last_played_at`, backfilled from `video_play` events. |
| `0007_profile_feature_switches.py` | Per-profile `allow_comments`, `allow_reactions`, `allow_tags`, `allow_customize`, `allow_upload`, `auto_subtitles` (existing profiles keep everything on). |
| `0008_login_failures.py` | `login_failures`, so the parent login limit survives a restart. |
| `0009_profile_pin_style.py` | `profiles.pin_style` (digits or pictures). |
| `0010_profile_language.py` | `profiles.language` for the kid screens. |
| `0011_wishlist.py` | `profiles.allow_wishlist` and `wishlist_requests`. |
| `0012_imports.py` | `videos.origin`, `origin_ref`, `source_path`, `episode_key`; the `import_jobs` table. |
| `0013_wishlist_details.py` | `wishlist_requests` keeps YouTube's title, description, upload date and views, plus the kid's own title, description and channel (`target_channel_id`). |

Tables (see `schema.sql` for columns): `channels` (parent-created when
`owner_profile_id` is NULL, kid-owned otherwise, plus kid channel art),
`sources`, `videos`, `profiles`, `profile_channels` (whitelist),
`reactions`, `comments`, `events` (append-only usage log),
`profile_video_overrides`, `tags`, `profile_video_tags`,
`profile_channel_videos`, `wishlist_requests`, `login_failures`,
`import_jobs`.

`videos` carries two independent states: `status` (`active` / `hidden` /
`review`, the parent's decision) and `download_status` (`pending` /
`downloading` / `ready` / `error` / `evicted`, the file's state). The
kid UI shows only `active` + `ready`.

### Kid library personalization

Each kid gets their own view of the shared library without copying data:

- **Title + thumbnail overrides** (`profile_video_overrides`) are overlays.
  `KidLibraryService.apply_overrides()` patches titles for that kid's
  request; `/media/thumb/{id}` serves
  `thumbnails/profiles/<profile_id>/<video_id>.*` before the canonical
  thumbnail. Siblings and the parent see the canonical version.
- **Tags** are per kid: `tags` is a shared name registry, but
  `profile_video_tags` is keyed on `profile_id`.
- **Channel bookmarks** (`profile_channel_videos`) let a kid add any video
  they can see to one of their own channels without moving the video.
  Kid-owned channels have banner/icon/colour art served by
  `/media/channel/{id}/banner` and `/icon`.

---

## Downloads

All downloads — a new add, the startup resume, a re-download after
eviction, a parent's Retry — go through `DownloadQueue`
(`app/services/download_queue.py`), one process-wide thread pool sized by
`storage.max_concurrent_downloads` (default 1). A video already queued or
running is not queued again. Queued rows stay `download_status='pending'`
and become `downloading` when a worker picks them up. Each job opens its
own SQLite connection.

yt-dlp runs with `noplaylist` and a size cap (`storage.max_download_bytes`,
default 4 GB). The backend's `last_error` is per thread and reset on each
call, so concurrent jobs can't report each other's errors. Errors are
stored as `[type] message`; the parent UI turns the type into plain advice.

### Cache eviction

Every video is kept unless the parent turns on **Freeing up space**
(`storage.cache_days > 0`, off by default). Then downloaded videos
(`storage_mode='cache'`, `keep_forever=0`) nobody watched for that many
days are evicted. Every play sets `videos.last_played_at`; age is
`COALESCE(last_played_at, cached_at)`.

- The maintenance loop runs the eviction; nothing else deletes videos,
  and a full disk never does. Instead `storage_report.disk_level` turns
  `warning` at `storage.low_space_percent` free (15) and `blocked` below
  `min_free_disk_bytes`, and the dashboard lists the ways out
  (`parent/_low_space.html`).
- Eviction re-checks eligibility under the per-video lock, deletes the
  files and sets `download_status='evicted'`. Uploads
  (`storage_mode='uploaded'`) and `keep_forever` videos are never evicted.

When a kid opens an evicted video, `ContentService.try_rehydrate_evicted`
atomically flips it back to `pending` (two taps queue one download) and
the kid sees `kid/preparing.html`, which keeps refreshing while the video
is pending or downloading. A re-download is compared with the length the
parent approved; if it differs by more than 10 s **and** 5 % the URL now
serves something else, and the video goes to `status='review'`.

Canonical thumbnails live at `thumbnails/videos/<id>.jpg`, outside the
video directory, so evicting a video keeps the picture the parent chose
(older `videos/<id>/thumb.jpg` files are moved there on first access).

### Playback baseline

Modern sources serve VP9/AV1 + Opus, which Safari and old devices can't
decode. After `StorageService.download`, in the shared ingest path for
uploads and imports, `MediaNormalizer` probes the file and leaves it alone
(already baseline),
**remuxes** it (`-c copy -movflags +faststart`), or **transcodes** it
(`libx264` main / `aac` / ≤720p / ≤30 fps). The result is a `video.mp4`
that is H.264/AAC ≤720p30 with `+faststart`. It never raises on media
problems: an unconvertible file keeps its original `.webm`/`.mkv`, which
playback, size accounting and crash recovery all find. The yt-dlp format
string prefers `avc1`+`mp4a`, so most ingests need only the remux. Design
notes: [`history/ui-and-playback-plan.md`](history/ui-and-playback-plan.md).

The command maps exactly one video stream (skipping cover-art pictures)
and one audio stream (the first, or the one the parent chose), and drops
subtitle and data streams (`-sn -dn`); a picture subtitle track can be
drawn into the video with an `overlay` filter. `convert(src, out)` writes
the result elsewhere and never touches the source; `normalize(path)`
converts in place.

ffmpeg runs through `app/services/ffmpeg_runner.py`: its own process
group, `-progress pipe:1` parsed on a reader thread that keeps only the
latest numbers, stderr to a log file. Callers get measured progress, a
speed averaged over the last minute and the time left. A `CancelToken`
or 2 minutes without progress stops the whole group (SIGTERM, then
SIGKILL) and the partial output is deleted. There is no fixed timeout.

### Subtitles

Subtitle files sit next to the video file as `video.<lang>.vtt` (or
`.srt`), in `videos/<id>/` for downloads and `uploads/<id>/` for uploads
and imports (`StorageService.subtitle_dir`). `/media/subs/<id>` lists
them with a language code for `srclang` and a readable name;
`/media/subs/<id>/<file>` always serves WebVTT, converting SubRip on the
fly (`app/services/subtitles.py`: times, ASS override tags, markup other
than i/b/u, Windows-1252 and UTF-16 text) and stripping VTT cue settings.

## Imports from a drive

`app/features/parent_import/` plus `app/services/imports.py`,
`import_scan.py`, `ingest.py` and `lookup.py`.

- **Scan.** `ImportRoots` lists video files under `storage.import_roots`,
  resolving every path inside its root and skipping symlinks, hidden
  files and the library's own folders. `parse_name` guesses title, year,
  season and episode; `find_sidecars` picks up posters, a Kodi `.nfo`
  and `.srt`/`.vtt` files next to the video.
- **Draft.** Picking files creates `import_jobs` rows in state `draft`
  (one batch). A background thread probes each file once (streams,
  conversion plan, default audio track) and fills in the details. The
  parent edits them; episodes of one show can become one new channel,
  ordered by `videos.episode_key` (season × 1000 + episode), which
  `list_ready(channel_id=…)` sorts by.
- **Lookup.** `LookupService` asks Wikipedia (REST search and page
  summary, in the kid language first) and Wikidata (film / TV series,
  year, MPAA rating), TMDB when a key is set, YouTube through the
  backend, or a page's `og:` tags. Candidate pictures are downloaded by
  the server into `uploads/.tmp/import-preview-<id>/` and shown from
  there, so the CSP stays `img-src 'self'`.
- **Run.** `ImportWorker` (one thread, started in the lifespan) takes the
  next `queued` job and runs it on the download pool, so imports share
  `max_concurrent_downloads`. `ImportRunner` re-checks the path, hashes
  the file (`up_<sha256[:16]>`, the same id as uploads, so a file is never
  imported twice), converts with `convert()` or copies it into
  `uploads/.tmp/import-<id>/` (a same-drive move with nothing to convert
  is a rename at commit), extracts the chosen subtitles to VTT, and then
  `IngestService.commit()` moves everything into `uploads/<id>/` and
  inserts the row. Progress goes to the job row about once a second; the
  page polls `/parent/import/status.json`.
- **Cancel and restart.** Cancel marks the row `cancelling` and fires the
  job's `CancelToken`; the runner also re-reads the row every few seconds.
  Either way the job folder is deleted and the source is untouched. On
  startup `working` jobs return to `queued`, `cancelling` ones become
  `cancelled`, and job folders no live job owns are deleted.

### Media folder

`config.media_dir` is `storage.media_path` when set, else the data dir.
`StorageService`, `ThumbnailService`, `ChannelService`, `KidLibraryService`,
the storage report and crash recovery all take the media dir; the
database, config, logs and update flag files stay in the data dir. A test
fails if code joins `videos`/`uploads`/`thumbnails`/`channels` onto the
data dir again.

When `storage.media_path` is set but the drive is not mounted
(`media_fs.media_missing`), the app runs degraded: kid pages show
`kid/resting.html` with a 503, the parent dashboard shows a banner and
keeps working so the drive can be reconnected, and startup jobs, crash
recovery and cache eviction skip all media work. Nothing is ever
written to the empty mount point. On a FAT32 drive,
`media_fs.capped` limits upload and download sizes to 4 GB.

---

## Startup and background work

`app/lifecycle.py` holds everything that isn't a request. `create_app()`
calls, in order:

1. `prepare_data_dir(config)` — create the session secret if missing, run
   pending migrations (`init_schema`), then `recover_from_crash`.
2. `startup_jobs(config)` — re-queue `pending` downloads on the
   `DownloadQueue` and sweep upload temp files older than 24 h. Failures
   are logged, never fatal.
3. `make_lifespan(config)` — the FastAPI lifespan: starts the mDNS
   advertiser, the **import worker** and the **maintenance loop**, and
   stops them on shutdown.

The maintenance loop wakes every `storage.cache_cleanup_interval_minutes`
(default 60; 0 disables it), re-reads the config, and on a worker thread
evicts expired cache videos and deletes usage events older than
`storage.events_retention_days` (default 365; 0 keeps them).

Everything in this module opens its own SQLite connection.

---

## Request pipeline

### Middleware order

Starlette makes the **last** `add_middleware` call the outermost layer.
From outermost to innermost:

1. **`HostCheckMiddleware`** — answers only when the `Host` header is
   an IP address or one of the box's own names (hostname with `.local`,
   `.lan`, `.home` and similar suffixes, `localhost`,
   `server.allowed_hosts`, `CURATABLES_ALLOWED_HOSTS`); anything else
   gets a 400. This stops DNS rebinding.
2. **`BodySizeLimitMiddleware`** — rejects oversized bodies by
   `Content-Length` and while streaming. It must be outermost because
   anything that reads the body before it (CSRF parses form bodies)
   would buffer an oversized request into memory first. Limits are per
   route and read from config on each request: parent tus uploads →
   `max_upload_bytes`; kid `POST /upload` → `max_kid_upload_bytes`;
   picture edits and curation-list imports → 6 MB; everything else → 1 MB.
3. **`PrometheusMiddleware`** — sees the final status code (no-op unless
   `server.prometheus_enabled`).
4. **`SessionMiddleware`** — signed cookie, `SameSite=Strict`.
5. **`CSRFMiddleware`** — validates the per-session token on POST/PUT/
   PATCH/DELETE, except a short audited list in `app/middleware/csrf.py`
   (exactly `/parent/setup`; login, profile select/PIN, `/api/log` and
   parent tus uploads by prefix). Exempt requests must not carry a
   cross-site `Origin`. A token in a header is checked without reading
   the body; otherwise a plain form is read whole (the body limit keeps
   it small), a multipart upload only until its token field (at most
   64 KB), and what was read is replayed to the route. Tokens live as long as the session.
6. `media_missing_screen` — the "videos are resting" page while the
   videos drive is missing (see [Media folder](#media-folder)).
7. `anonymous_kid_redirect` — logged-out visitors on kid pages go to
   `/profiles`.
8. `system_setup_redirect` — on the Pi image, parent pages go to setup
   step 2 (`/parent/setup/system`) until the Linux password is chosen.
9. `first_run_redirect` — everything except setup, static and
   `/healthz` goes to `/parent/setup` until a password exists.
10. `security_headers` — CSP, `X-Frame-Options`, `nosniff`, referrer
    policy.
11. **`RequestIDMiddleware`** — per-request correlation ID, injected into
    every log record.
12. `RequestLoggingMiddleware` — access log line.

### Errors

404/405, validation errors and unhandled exceptions render the kid error
page (or `parent/error.html` under `/parent/`). `/api/`, `/media/`,
`/healthz`, `/metrics` and the tus endpoints keep JSON.

### Kid pages and JavaScript

- Every kid page extends `kid/_shell.html` (lang, theme CSS,
  `<header>/<nav>/<main>`) and is rendered with `render_kid_template`.
- Kid JavaScript lives in `app/static/kid/*.js` (`watch.js`, `pin.js`) and
  must be **ES3**: no `const`/`let`, no arrow functions, no `fetch`; use XHR.
  `tests/test_kid_js_es3.py` scans those files and inline kid `<script>`
  blocks. Forms still work without JS through a plain POST + redirect.
- Static asset URLs carry a content fingerprint, `?v={{ asset_v }}`
  (computed once at startup in `template_utils._asset_version`), so
  browsers fetch new CSS/JS after an upgrade.

### Theming

Themes are CSS only. `app/static/kid/style.css` declares custom properties
in `:root` and keeps literal fallbacks for iOS 9, which ignores custom
properties. `kid/_shell.html` adds `theme-<name>.css` for the profile's
theme (`playful`, `calm`, `clear`); `base` uses the stylesheet alone.
Adding a theme means a new `theme-<name>.css`, an entry in
`app/constants.py:THEME_CHOICES`, and the name in `_shell.html`'s list.
Don't fork templates per theme.

---

## In-app yt-dlp update

The service unit (`systemd/curatables.service`) is sandboxed:
`ProtectSystem=strict` with `ReadWritePaths=<data dir>` means the app can
write **only** its data dir — the venv is read-only to it — and
`NoNewPrivileges=true` means it cannot `sudo` or restart itself. So the
dashboard's "Update yt-dlp" button cannot pip-install directly. Instead:

1. The app (`app/services/updates.py`) atomically writes
   `update-request.json` in the data dir. It never shells out.
2. The root-owned `curatables-updater.path` unit sees the file and starts
   `curatables-updater.service`, which runs `scripts/updater.sh` as root.
3. `updater.sh` does one fixed thing: `pip install --upgrade yt-dlp`. It
   reads the request and writes `update-result.json` **as the service
   user** (the data dir belongs to that user, so root acting there by path
   could be redirected by a planted symlink), and runs pip **as the venv
   owner** (root on a normal install, where `/opt/curatables` is
   root-owned), so root never executes files the service user can edit.
   Then it removes the request and `systemctl restart curatables`.
4. The dashboard reads the result file on its next render.

Don't weaken the sandbox to add self-update. The request carries a
`kind` field (only `yt-dlp` is accepted). Updating Curatables itself
goes through the system helper instead (next section). User-facing
flow: [`upgrade.md`](upgrade.md).

---

## System helper

On the Pi image the dashboard's Box pages (Linux password, SSH, Wi-Fi,
drives, backups, diagnostics, updates, restart and shut down) need root.
The web app never gets it. `syshelper/` is a separate root daemon
(`curatables-helper.service`, run by `scripts/curatables-helper.py` on
the system Python, standard library only).

- **Socket.** `/run/curatables-helper/helper.sock`, `root:curatables`
  0660, and `SO_PEERCRED` must name the service user or root. One JSON
  request and one JSON reply per connection. The app side is
  `app/services/system_helper.py` (`SystemHelper.call`); when the socket
  is absent (own installs, tests) `available` is false and the Box pages
  explain that they need the Pi image.
- **Actions.** A fixed list, each registered with
  `@action("name")` in its module (`syshelper/registry.py`). Parameters
  are validated with `registry.text`/`flag` and passed to programs as
  separate arguments (`runner.run`); nothing goes through a shell.
  Anything slow (Wi-Fi switch, drive work, backup restore, updates) is a
  `job=True` action: it returns a job id at once, runs on a thread, one
  at a time, and the page polls `job_status` through
  `/parent/system/jobs/<id>.json`.
- **Service-owned files.** The data dir belongs to the service user, so
  root must not follow paths there. `syshelper/safefs.py` reads and
  writes with `O_NOFOLLOW` and an ownership check; copies into those
  folders run as the service user via `runuser`.
- **Wi-Fi.** `wifi_set` creates a trial NetworkManager profile and tries
  it twice; if the box doesn't connect, it goes back to the previous
  connection. `/run/curatables-wifi-test` tells `nm-wired-only.sh` to
  stay out of the way meanwhile.
- **SD-card recovery.** `curatables-recovery.service` and
  `curatables-diagnose.service` run `curatables-helper.py --reset-from-boot`
  and `--report-from-boot` at boot when `curatables-reset.txt` or
  `curatables-diagnose.txt` is on the boot partition.

Rules for new actions: validate every parameter, never build a shell
string, never act as root on a path the service user can change, and add
a test to `tests/test_syshelper*.py`. The helper is root, so a
compromised app can do whatever the actions allow, and no more; see
[`threat-model.md`](threat-model.md).

### Updating Curatables

`syshelper/upgrade.py` (`curatables_update`, `curatables_rollback`):

1. Fetch the latest GitHub release, its `curatables-<version>.tar.gz`
   and `SHA256SUMS` (HTTPS only, `syshelper/fetch.py`), and check the
   hash.
2. Unpack it safely into `/opt/curatables-<version>-<stamp>`, build its
   venv there and run `run.py --check`.
3. Take a backup, then point the `/opt/curatables` symlink at the new
   folder (`/opt/curatables.previous` points at the old one) and run
   `scripts/apply-system.sh` so new units and system files are
   installed.
4. Wait up to 180 s for `/healthz` to report the new version. If it
   doesn't, switch back; the database backup is restored only when the
   new version added migrations.

A `pending-update.json` marker in a root-only folder lets the helper
finish or undo an update interrupted by a power cut when it next starts.
`system_update` runs `apt` for Debian packages; unattended-upgrades
installs security updates on its own. Releases come from
`.github/workflows/release-source.yml`, which refuses a tag that doesn't
match the version in the code.

---

## Video Backend Abstraction

All interaction with video sites goes through the `VideoBackend` interface
(`app/backends/base.py`); the only implementation is `YtdlpBackend`
(`app/backends/ytdlp.py`). Swapping to a fork or another tool means one
new class and one line in `app/dependencies.py:get_backend()`.

The interface is URL-based so every yt-dlp extractor works the same way:
`fetch_video_info(url)`, `fetch_channel_videos(url, max_results)`,
`fetch_playlist(url, max_results)`, `download_video(url, output_dir,
resolution, subtitle_langs)`. The returned `VideoMetadata` carries
`extractor` and `original_url`, which build the composite stored ID
`{extractor}_{raw_id}` (`app/services/ids.py:make_video_id`; IDs that had
to be sanitised get a short hash suffix so two can't collide).

---

## Crash Recovery & Data Integrity

The server must survive unclean shutdowns (power loss, killed process,
unplugged drive) without corruption or stuck state.

1. **SQLite WAL + `busy_timeout=5000`** (`app/db/connection.py`). Queue
   workers, the maintenance loop and requests all write; each waits up to
   5 s for a lock instead of failing.
2. **Atomic config writes.** `Config.save()` writes a `.tmp`, `fsync`s and
   renames.
3. **`recover_from_crash`** (`app/db/schema.py`), on every start:
   `downloading` → `pending`; `.part` files deleted; `ready` videos whose
   file is missing → `pending`. If most `ready` videos are missing at once
   (an unmounted external drive) it changes nothing and logs why; if the
   media folder itself is missing it skips the file checks altogether.
4. **Mount guard.** With `CURATABLES_REQUIRE_MOUNT=1` (set by
   `scripts/setup-external-drive.sh`) `run.py` refuses to start when the
   data dir is not a mount point, rather than serving an empty library
   with first-run setup open. The systemd units also carry
   `RequiresMountsFor=` on the data dir.
5. **Per-video download lock** in `StorageService`, plus the queue's
   de-duplication.
6. **Backups.** `scripts/backup.sh` / `restore.sh` and the nightly
   `curatables-backup.timer`; see [`backup.md`](backup.md).
7. **Graceful shutdown cap.** `uvicorn.run(..., timeout_graceful_shutdown=3)`
   so open `<video>` keep-alive sockets don't block Ctrl+C.

Design rule: no operation may leave the system needing manual repair. If
the server crashes at any point, the next startup recovers automatically.

### Concurrency

- Per-request connections (`get_db`) share nothing between requests;
  `check_same_thread=False` lets worker threads use their own.
- Concurrent upserts are race-free: reactions use
  `INSERT ... ON CONFLICT DO UPDATE`, bookmarks and tags `INSERT OR IGNORE`,
  overrides try-insert-then-update.

---

## Shared curation (export / import)

Parents share curated channels by file. Export is
`GET /parent/channels/{id}/export?format=ytc|txt|pdf`, import is
`GET`/`POST /parent/channels/import` (`app/features/parent_sharing/`). All
encode/decode/render logic is in `app/services/sharing.py`.

- **`.ytc`** is JSON with a mandatory `"schema": "curatables.ytc/1"`. Decode
  is strict on the schema version, lenient on unknown keys.
- **Re-fetch, don't trust.** Every imported URL is re-fetched via yt-dlp;
  titles in the file are never shown as the source of truth.
- **Review-gated.** Import lands on the same preview page as the add flow,
  with the same "I have already fully watched this video" gate.
- **PDF is optional.** Without `reportlab` the PDF route returns 503; `.ytc`
  and `.txt` keep working.

---

## Adding a New Feature (contributor guide)

Example: a "favorites" feature where kids can star videos.

1. **Schema:** `app/db/migrations/NNNN_favorites.sql` with the
   `CREATE TABLE`; update `app/db/schema.sql` to match.
2. **Model:** `app/models/favorite.py`.
3. **Repository:** `app/repositories/favorite_repo.py`.
4. **Service:** extend an existing service or add `app/services/favorites.py`.
   Use `ContentService.can_view` for anything that touches videos.
5. **Feature:** copy `app/features/_template/` to `app/features/kid_favorites/`.
   If it should be switchable per kid, add a profile column (migration) and
   guard the routes with `require_kid_feature(...)`.
6. **Wire:** `app.include_router(...)` in `app/main.py`; providers in
   `app/dependencies.py`.
7. **Templates/JS:** `app/templates/base/kid/favorites.html` extending
   `kid/base.html`; any JS in `app/static/kid/` (ES3).
8. **Tests:** `tests/test_favorites.py`.

---

## Testing Strategy

- **Repository and service tests** use in-memory SQLite with every
  migration applied (`tests/conftest.py`). Real repositories, no mocking.
- **Route tests** use FastAPI's `TestClient` on a full `create_app()` with
  `get_db` overridden. Auth is enforced as in production; CSRF validation
  is stubbed in the fixture and exercised for real in `tests/test_csrf.py`.
- **Guard tests** pin cross-cutting rules: ES3 kid JS, the
  `TemplateResponse(request, ...)` signature, the yt-dlp codec preference.
- **System helper.** `tests/test_syshelper*.py` call the actions
  directly with `SETTINGS` pointed at temporary files and programs
  stubbed out. Route tests for the Box pages replace
  `app.state.system_helper` with `tests/fake_helper.py`.
- **Smoke suite** (`tests/test_smoke_e2e.py`, `pytest -m smoke`, excluded
  by default) boots a real uvicorn subprocess against a fake yt-dlp and
  walks setup → kid grid → add flow.

Details: [`CONTRIBUTING.md`](../CONTRIBUTING.md).

---

## Not built (yet)

The architecture can take these without restructuring; none exist today.

- **Plugin system.** Services can become swappable interfaces through the
  DI layer.
- **WebSocket push.** A new feature directory.
- **Content-safety agent / LLM features.** Designed in
  [`design/v0.6-safety-agent.md`](design/v0.6-safety-agent.md).
- **Filtered real-time proxy, LLM filter layers, offline web pages.**
  See [`design/`](design/). The wishlist (Mode A) exists:
  `app/features/kid_wishlist/`, `app/features/parent_requests/`,
  `app/services/wishlist.py`.
- **Source subscriptions** (auto-sync of channels/playlists).

---

## Inspired By

- **Navidrome** — clean architecture with model/persistence/core/adapter layers
- **Jellyfin** — interface-based DI, per-user display preferences, plugin contracts
- **Invidious** — YouTube proxy architecture, domain-per-directory structure
- **AdGuard Home** — one-package-per-concern, thorough contributor documentation
- **FastAPI full-stack template** — repository pattern, dependency injection,
  feature-based routing
