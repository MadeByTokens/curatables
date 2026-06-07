# curatables — Architecture

This document describes the software architecture of curatables. Every contributor
should read this before writing code. It explains **why** things are where they are,
not just where they are.

For operational concerns (running Curatables as a long-lived service,
binding port 80, and the mDNS/`.local` discovery story) see
[`deployment.md`](deployment.md). For the canonical list of every
package, binary, filesystem path, port and capability Curatables
needs — the bill of materials a Dockerfile or install script would
consume — see [`dependencies.md`](dependencies.md).

---

## Design Principles

1. **Feature isolation.** Each feature lives in its own directory. A pull request
   that adds "playlist support" should touch `app/features/playlists/` and one
   line in `app/main.py`. Nothing else.

2. **Three-layer separation.** Routes parse HTTP. Services hold business logic.
   Repositories own database queries. No layer may skip another — a route never
   executes SQL, a repository never raises an HTTP error.

3. **Viewer context everywhere.** Every request carries a `ViewerContext` that
   says who is looking (parent, child profile, anonymous) and what they can see.
   Features query the context instead of reimplementing access checks.

4. **Data model supports the full vision.** The database schema covers child
   profiles, internal channels, content sources, usage events, and per-profile
   preferences from day one — even if the UI doesn't expose all of them yet.

5. **Old-device-first frontend.** The kid-facing UI must work on Safari iOS 9
   and old Android WebView. No ES6+, no CSS grid, no frameworks. The parent
   dashboard has no such constraint but stays server-rendered for simplicity.

6. **Theming via directory overlay.** Themes are directories of templates and CSS
   that override the base. Switching a child's theme is a config change, not a
   code change.

7. **No magic.** Explicit imports, explicit wiring, no classpath scanning, no
   decorators that hide control flow. A new contributor should be able to trace
   a request from URL to database in under two minutes.

---

## Project Structure

```
curatables/
├── app/                            # All server code lives here
│   ├── main.py                     # FastAPI app factory, router wiring, lifespan
│   ├── config.py                   # Configuration loading/saving (dataclass + JSON file)
│   ├── dependencies.py             # Shared FastAPI Depends() — db, viewer context, services
│   │
│   ├── middleware/                 # ASGI middleware package
│   │   ├── body_size.py            # Request body size limit
│   │   ├── csrf.py                 # CSRF tokens (SameSite=Strict + signed per-session)
│   │   └── request_id.py           # Per-request correlation ID + log binding
│   │
│   ├── db/
│   │   ├── connection.py           # SQLite connection management (WAL, row_factory)
│   │   ├── schema.sql              # Snapshot of the current schema (reference only)
│   │   ├── schema.py               # init_schema shim + recover_from_crash
│   │   ├── migrator.py             # Forward-only migration runner
│   │   └── migrations/             # Numbered migration files (NNNN_slug.sql / .py)
│   │
│   ├── models/                     # Data classes — no logic, no imports from other layers
│   │   ├── video.py                # Video, VideoSummary
│   │   ├── channel.py              # InternalChannel
│   │   ├── source.py               # ContentSource (YouTube channel/playlist/video)
│   │   ├── profile.py              # ChildProfile, ProfilePreferences
│   │   ├── event.py                # UsageEvent
│   │   └── viewer.py               # ViewerContext (injected into every request)
│   │
│   ├── repositories/               # Database access — SQL lives here and nowhere else
│   │   ├── base.py                 # BaseRepository with shared helpers
│   │   ├── video_repo.py           # VideoRepository
│   │   ├── channel_repo.py         # ChannelRepository (internal channels)
│   │   ├── source_repo.py          # SourceRepository (upstream sources, any platform)
│   │   ├── profile_repo.py         # ProfileRepository
│   │   └── event_repo.py           # EventRepository
│   │
│   ├── backends/                   # Video source abstraction (yt-dlp today, replaceable)
│   │   ├── base.py                 # VideoBackend abstract interface
│   │   └── ytdlp.py                # yt-dlp implementation
│   │
│   ├── services/                   # Business logic — no HTTP, no SQL, no yt-dlp
│   │   ├── auth.py                 # Password hashing, session management
│   │   ├── content.py              # Add/remove/edit approved content, resolve sources
│   │   ├── video_source.py         # Platform-agnostic URL parsing + VideoBackend delegate
│   │   ├── ids.py                  # Composite video_id ({extractor}_{raw_id}) helpers
│   │   ├── embeds.py               # Per-extractor iframe embed URL builder (Tier 1 allow-list)
│   │   ├── storage.py              # Video file management, caching, disk quota guard
│   │   ├── storage_report.py       # Read-only disk usage reporting (reused by topnav chip)
│   │   ├── uploads.py              # Upload lifecycle (tus create/append/finalize), viewer-agnostic
│   │   ├── media_probe.py          # ffprobe wrapper (codec/fps/faststart) + ffmpeg decoder allow-list
│   │   ├── normalize.py            # Transcode/remux ingest to the client playback baseline (H.264/AAC ≤720p30 +faststart)
│   │   ├── relocation.py           # Data directory relocation with preflight checks
│   │   ├── thumbnails.py           # Thumbnail fetch, extract frame, delete, custom upload
│   │   ├── events.py               # Usage logging and statistics aggregation
│   │   ├── channels.py             # Internal channel management, kid-owned channel helpers
│   │   ├── reactions.py            # Emoji reactions
│   │   ├── comments.py             # Family comments
│   │   ├── profiles.py             # Child profile management, preferences
│   │   ├── mdns.py                 # Zeroconf / _http._tcp.local. advertisement
│   │   ├── sharing.py              # .ytc / text / PDF encode/decode for shared curation
│   │   ├── kid_library.py          # Per-kid title/thumbnail overrides, tags, channel bookmarks
│   │   ├── stats.py                # Stats dashboard aggregation (windows, per-kid/per-video)
│   │   ├── metrics.py              # Prometheus counters (opt-in via config)
│   │   ├── rate_limit.py           # In-memory rate limiting (comment posts)
│   │   └── csrf.py                 # CSRF token issue/validate (used by the CSRF middleware)
│   │
│   ├── features/                   # HTTP layer — routes grouped by feature
│   │   ├── _template/              # Skeleton for new features (copy to start)
│   │   │   ├── router.py
│   │   │   ├── schemas.py
│   │   │   └── README.md
│   │   │
│   │   ├── parent_auth/            # Setup wizard, login, logout
│   │   │   └── router.py
│   │   │
│   │   ├── parent_dashboard/       # Mission-control landing page
│   │   │   └── router.py
│   │   │
│   │   ├── parent_content/         # Add, edit, list, hide, delete content
│   │   │   ├── router.py
│   │   │   └── schemas.py          # Form validation models
│   │   │
│   │   ├── parent_channels/        # Manage internal channels
│   │   │   └── router.py
│   │   │
│   │   ├── parent_sharing/         # Export .ytc/.txt/.pdf; import .ytc/.txt
│   │   │   └── router.py
│   │   │
│   │   ├── parent_profiles/        # Manage child profiles
│   │   │   └── router.py
│   │   │
│   │   ├── parent_settings/        # Server config, password, storage, advanced settings
│   │   │   └── router.py
│   │   │
│   │   ├── parent_stats/           # Usage statistics dashboard
│   │   │   └── router.py
│   │   │
│   │   ├── parent_storage/         # Disk usage report + per-channel size breakdown
│   │   │   └── router.py
│   │   │
│   │   ├── parent_uploads/         # Resumable tus.io 1.0.0 upload endpoints
│   │   │   └── router.py
│   │   │
│   │   ├── kid_profiles/           # Profile picker, PIN entry, switch
│   │   │   └── router.py
│   │   │
│   │   ├── kid_browse/             # Home page, channel view, pagination
│   │   │   └── router.py
│   │   │
│   │   ├── kid_watch/              # Video player page
│   │   │   └── router.py
│   │   │
│   │   ├── kid_search/             # Search (when enabled for profile)
│   │   │   └── router.py
│   │   │
│   │   ├── kid_comments/           # Family comments on videos
│   │   │   └── router.py
│   │   │
│   │   ├── kid_uploads/            # Plain XHR multipart uploads + kid-created channels
│   │   │   └── router.py
│   │   │
│   │   ├── media/                  # Video streaming, thumbnail serving
│   │   │   └── router.py
│   │   │
│   │   └── api/                    # JSON API for frontend JS (logging, etc.)
│   │       └── router.py
│   │
│   ├── templates/
│   │   ├── base/                   # Default templates (always present)
│   │   │   ├── kid/
│   │   │   │   ├── base.html
│   │   │   │   ├── home.html
│   │   │   │   ├── watch.html
│   │   │   │   ├── search.html
│   │   │   │   ├── upload.html
│   │   │   │   ├── profiles.html
│   │   │   │   ├── pin.html
│   │   │   │   ├── error.html
│   │   │   │   └── help.html
│   │   │   └── parent/
│   │   │       ├── base.html
│   │   │       ├── login.html
│   │   │       ├── setup.html
│   │   │       ├── dashboard.html
│   │   │       ├── content.html
│   │   │       ├── add.html
│   │   │       ├── content_preview.html
│   │   │       ├── content_edit.html
│   │   │       ├── channels.html
│   │   │       ├── channel_edit.html
│   │   │       ├── profiles.html
│   │   │       ├── profile_form.html
│   │   │       ├── settings.html
│   │   │       ├── stats.html
│   │   │       ├── storage.html
│   │   │       ├── upload.html
│   │   │       └── help.html
│   │   │
│   │   └── themes/                 # Optional template overrides per theme
│   │       ├── playful/            # (empty — CSS-only theme)
│   │       └── calm/               # (empty — CSS-only theme)
│   │
│   └── static/
│       ├── kid/
│       │   ├── style.css           # Base kid styles (old-browser-safe)
│       │   ├── theme-playful.css   # :root variable overrides for "playful"
│       │   └── theme-calm.css      # :root variable overrides for "calm"
│       └── parent/
│           └── style.css           # Parent dashboard styles
│
├── tests/                          # Mirrors app/ structure
│   ├── conftest.py                 # Fixtures: test db, test app, test client
│   ├── repositories/
│   ├── services/
│   └── features/
│
├── curatables-cli.py                # Standalone CLI tool (independent of server)
├── run.py                          # Entry point: python run.py
├── pyproject.toml                  # Package metadata + build config + tool config
├── requirements.txt                # Production dependencies
├── requirements-dev.txt            # Test/lint dependencies
├── README.md                       # Project landing page
├── LICENSE                         # Apache 2.0
├── .github/
│   └── workflows/
│       └── ci.yml                  # Test matrix (3.10/3.11/3.12), mypy, wheel build
├── scripts/                        # install.sh + ops scripts (backup.sh, restore.sh)
├── systemd/                        # Service unit + install/uninstall helpers
├── docs/
│   ├── architecture.md             # This file
│   ├── deployment.md               # systemd, mDNS, port 80
│   ├── dependencies.md             # Bill of materials
│   ├── backup.md                   # Backup + restore ops guide
│   ├── prd.md                      # Product requirements (historical)
│   ├── filtered-search-and-proxy.md
│   ├── open-source-media-center-features.md
│   ├── search-research-playlists-at.md
│   └── research/                   # Primary-source artifacts cited by docs
│       └── playlists_at_script.js
└── ...
```

---

## Layers in Detail

### 1. Models (`app/models/`)

Plain Python dataclasses. No methods beyond `__init__`. No imports from other
layers. These are the shared vocabulary of the entire application.

```python
@dataclass
class Video:
    video_id: str
    title: str
    channel_name: str
    description: str
    duration: int
    thumbnail_url: str
    status: str          # "active" | "hidden" | "archived"
    channel_id: int | None
    resolution: str
    cached_at: str | None
    # ...

@dataclass
class ViewerContext:
    viewer_type: str     # "parent" | "child" | "anonymous"
    profile_id: int | None
    profile_name: str
    allowed_channel_ids: list[int] | None   # None = all
    search_mode: str     # "disabled" | "curated" | "open"
    theme: str           # "base" | "playful" | "calm"
```

### 2. Repositories (`app/repositories/`)

Each repository owns the SQL for one table (or a small cluster of related
tables). Repositories accept a `sqlite3.Connection` and return model objects.

```python
class VideoRepository:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def get(self, video_id: str) -> Video | None: ...
    def list_active(self, limit: int, offset: int) -> list[Video]: ...
    def list_by_channel(self, channel_id: int) -> list[Video]: ...
    def count(self, status: str | None = None) -> int: ...
    def insert(self, video: Video) -> None: ...
    def update(self, video_id: str, **fields) -> None: ...
    def delete(self, video_id: str) -> None: ...
```

Rules:
- No business logic. A repository does not decide **whether** a video should
  be deleted — it just deletes.
- No HTTP concepts. No `Request`, no `Response`, no status codes.
- SQL is parameterized, never string-formatted.

### 3. Services (`app/services/`)

Services contain business logic. They call repositories (never raw SQL) and
other services. They raise domain exceptions (e.g., `VideoNotFound`,
`NotAuthorized`), never HTTP exceptions.

```python
class ContentService:
    def __init__(self, video_repo: VideoRepository,
                 source_repo: SourceRepository,
                 channel_repo: ChannelRepository,
                 source: VideoSourceService,
                 thumbnail_svc: ThumbnailService):
        self.video_repo = video_repo
        # ...

    def add_video_by_url(self, url: str, channel_id: int | None,
                         resolution: str) -> Video:
        """Fetch metadata, download thumbnail, insert into DB."""
        parsed = self.source.parse_url(url)
        info = self.source.fetch_video_info(parsed.clean_url)
        stored_id = make_video_id(info.extractor, info.video_id)
        self.thumbnail_svc.download(stored_id, info.thumbnail_url)
        video = Video(video_id=stored_id, extractor=info.extractor,
                      original_url=info.original_url,
                      title=info.title, ...)
        self.video_repo.insert(video)
        return video

    def list_for_viewer(self, viewer: ViewerContext,
                        page: int, per_page: int) -> tuple[list[Video], int]:
        """Return videos this viewer is allowed to see."""
        if viewer.allowed_channel_ids is not None:
            # Child with restricted channels
            return self.video_repo.list_by_channels(
                viewer.allowed_channel_ids, limit=per_page,
                offset=(page - 1) * per_page)
        return self.video_repo.list_active(
            limit=per_page, offset=(page - 1) * per_page)
```

Key services worth knowing about:

- **`ContentService`** — video add/list/search/moderation (above).
- **`ChannelService`** — channel CRUD and kid-visibility helpers.
- **`ReactionService`, `CommentService`** — emoji + threaded comments.
- **`StatsService`** — aggregated dashboard (KPIs, top videos, per-kid
  summary, per-video detail). Composes event, comment, reaction, and
  profile repos; never touches SQL directly.
- **`KidLibraryService`** — per-kid title/thumbnail overrides, personal
  tags + tag cloud, channel bookmarks. Composes `ProfileVideoOverrideRepository`,
  `TagRepository`, `ProfileChannelVideoRepository`, plus filesystem access
  for custom thumbnails. Its `apply_overrides(profile_id, videos)` method
  is called by the kid browse/watch/tag routes to patch `Video.title`
  for the current kid's view before the template renders.
- **`MediaNormalizer`** (`normalize.py`) — the ingest-time **playback
  baseline** guarantee. Modern sources serve VP9/AV1 video + Opus audio,
  which Safari and old devices can't decode; `merge_output_format=mp4`
  only re-containers, it doesn't transcode. So after `StorageService.download`
  (downloads) and inside `UploadService.finalize` (uploads), the pulled
  file is probed via `MediaProbeService` and then either left as-is
  (already baseline), **remuxed** (`-c copy -movflags +faststart`, no
  re-encode, when only the moov-atom position is wrong), or **transcoded**
  (`libx264` main / `aac` / scale ≤720p / cap 30 fps). The result is always
  a `video.mp4` that is H.264/AAC, ≤720p30, with `+faststart`. It never
  raises on media problems — an unreadable file is left untouched so the
  ingest still completes. This runs in the background download thread (and
  the upload finalize path), so it is off the request path; it is
  queue-driven / bursty by construction, fitting the idle-box thermal
  envelope. The yt-dlp format string (`app/backends/ytdlp.py`) prefers
  `avc1`+`mp4a` so most ingests need only the cheap remux, not a full
  transcode. Full design + verification: `docs/ui-and-playback-plan.md`.

### 4. Features / Routes (`app/features/`)

Each feature directory has a `router.py` that defines an `APIRouter`. Routes
are thin — they parse the request, call a service, and return a response.

```python
# app/features/kid_browse/router.py
router = APIRouter(tags=["kid"])

@router.get("/")
def home(request: Request,
         viewer: ViewerContext = Depends(get_viewer),
         content: ContentService = Depends(get_content_service)):
    videos, total = content.list_for_viewer(viewer, page=1, per_page=24)
    return render(request, viewer, "kid/home.html", {"videos": videos})
```

Rules:
- A route calls **one** service method (occasionally two).
- A route never calls a repository directly.
- A route never contains `if viewer.profile_type == ...` logic — that
  belongs in the service.

### 5. Dependencies (`app/dependencies.py`)

FastAPI's `Depends()` wires everything together. This is the only place where
the layers connect.

```python
def get_db(request: Request):
    return request.app.state.db

def get_viewer(request: Request, db=Depends(get_db)) -> ViewerContext:
    """Build a ViewerContext from the session cookie."""
    session = request.session
    if session.get("parent_authenticated"):
        return ViewerContext(viewer_type="parent", ...)
    profile_id = session.get("profile_id")
    if profile_id:
        profile = ProfileRepository(db).get(profile_id)
        return ViewerContext(viewer_type="child", ...)
    return ViewerContext(viewer_type="anonymous", ...)

def require_parent(viewer=Depends(get_viewer)) -> ViewerContext:
    if viewer.viewer_type != "parent":
        raise HTTPException(status_code=302, headers={"Location": "/parent/login"})
    return viewer

def get_video_repo(db=Depends(get_db)) -> VideoRepository:
    return VideoRepository(db)

def get_content_service(
    video_repo=Depends(get_video_repo),
    source_repo=Depends(get_source_repo),
    channel_repo=Depends(get_channel_repo),
    source=Depends(get_video_source_service),
    thumbs=Depends(get_thumbnail_service),
) -> ContentService:
    return ContentService(video_repo, source_repo, channel_repo, source, thumbs)
```

For testing: `app.dependency_overrides[get_db] = lambda: in_memory_db`.

---

## Database Schema

Schema changes are managed by a forward-only migrator. Each change is
a numbered file under `app/db/migrations/` (either `.sql` applied via
`executescript`, or `.py` with `def up(conn): ...` for logic that SQLite's
DDL can't express — e.g. conditional `ALTER TABLE ADD COLUMN`). The
migrator records applied versions in a `schema_migrations` table and
skips anything already recorded. `app/db/schema.sql` stays on disk as a
reference snapshot of the current canonical shape but is no longer
executed at runtime.

**Adding a column:** write `app/db/migrations/NNNN_slug.sql` with the
`ALTER TABLE`, update `schema.sql` to match the new shape. Done. Every
table is designed for the full PRD scope, not just the current MVP.

```sql
-- Parent-defined channels for organizing content in the kid UI
CREATE TABLE channels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    description      TEXT DEFAULT '',
    position         INTEGER DEFAULT 0,   -- display order
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    owner_profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    -- owner_profile_id: NULL = parent-created (visible to all profiles
    -- subject to profile_channels whitelist). Non-NULL = owned by a
    -- specific kid profile; only visible to that kid by default.
    banner_filename  TEXT,                -- kid channel art: banner.*
    icon_filename    TEXT,                -- kid channel art: icon.*
    color            TEXT DEFAULT '#2a9d8f'   -- accent color
);

-- Content sources (YouTube channels, playlists, individual videos)
CREATE TABLE sources (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type  TEXT NOT NULL CHECK(source_type IN ('channel','playlist','video')),
    youtube_id   TEXT NOT NULL UNIQUE,
    title        TEXT NOT NULL,
    description  TEXT DEFAULT '',
    url          TEXT NOT NULL,
    auto_sync    INTEGER DEFAULT 0,   -- future: auto-check for new uploads
    added_at     TEXT NOT NULL DEFAULT (datetime('now')),
    status       TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT DEFAULT '{}'
);

-- Individual approved videos
CREATE TABLE videos (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id       TEXT NOT NULL UNIQUE,   -- 11-char YouTube ID
    source_id      INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    channel_id     INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    title          TEXT NOT NULL,
    original_title TEXT NOT NULL,           -- YouTube's title, never edited
    channel_name   TEXT DEFAULT '',         -- YouTube channel name
    description    TEXT DEFAULT '',
    duration       INTEGER DEFAULT 0,
    upload_date    TEXT DEFAULT '',
    view_count     INTEGER DEFAULT 0,
    thumbnail_url  TEXT DEFAULT '',
    thumbnail_type TEXT DEFAULT 'original', -- 'original' | 'frame' | 'custom'
    status         TEXT NOT NULL DEFAULT 'active',
    storage_mode   TEXT DEFAULT 'cache',    -- 'cache' | 'library'
    resolution     TEXT DEFAULT '720p',
    added_at       TEXT NOT NULL DEFAULT (datetime('now')),
    cached_at      TEXT,
    cache_expires_at TEXT,
    file_size      INTEGER DEFAULT 0
);

-- Child profiles
CREATE TABLE profiles (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    pin          TEXT DEFAULT '',            -- optional PIN for profile selection
    avatar       TEXT DEFAULT 'default',     -- avatar identifier
    theme        TEXT DEFAULT 'base',        -- template theme name
    search_mode  TEXT DEFAULT 'disabled',    -- 'disabled' | 'curated' | 'open'
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Which channels each profile can see (many-to-many)
-- If a profile has NO rows here, it can see ALL channels.
CREATE TABLE profile_channels (
    profile_id  INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    channel_id  INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    PRIMARY KEY (profile_id, channel_id)
);

-- Usage events (append-only log)
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id  INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    event_type  TEXT NOT NULL,
    video_id    TEXT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    data_json   TEXT DEFAULT '{}'
);

-- Kid library personalization: per-kid title / thumbnail overlays on
-- any video the kid can see. No copies of the canonical video record
-- are made; this is a pure overlay applied at read time by KidLibraryService.
CREATE TABLE profile_video_overrides (
    profile_id       INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id         TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    title            TEXT,
    has_custom_thumb INTEGER DEFAULT 0,
    PRIMARY KEY (profile_id, video_id)
);

-- Shared tag name registry (case-insensitive uniqueness).
CREATE TABLE tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

-- Per-kid tag assignment junction. Each kid has their own independent
-- organizational tag system; tags added by one kid are invisible to
-- siblings.
CREATE TABLE profile_video_tags (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (profile_id, video_id, tag_id)
);

-- Per-kid channel bookmarks (YouTube-style playlists). A kid can add
-- any visible video to any of their own channels without moving the
-- canonical video.channel_id. Same video can appear in multiple kid
-- channels simultaneously.
CREATE TABLE profile_channel_videos (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    position   INTEGER DEFAULT 0,
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (profile_id, channel_id, video_id)
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source_id);
CREATE INDEX IF NOT EXISTS idx_events_profile ON events(profile_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_video ON events(video_id);
CREATE INDEX IF NOT EXISTS idx_profile_channels ON profile_channels(profile_id);
CREATE INDEX IF NOT EXISTS idx_pvt_profile ON profile_video_tags(profile_id);
CREATE INDEX IF NOT EXISTS idx_pvt_tag ON profile_video_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_pcv_channel ON profile_channel_videos(profile_id, channel_id);
```

### Kid library personalization model

The four tables above (`profile_video_overrides`, `tags`,
`profile_video_tags`, `profile_channel_videos`) plus the three new
columns on `channels` (`banner_filename`, `icon_filename`, `color`)
support a YouTube-like personalization experience where each kid gets
their own view of the shared video library without duplicating data:

- **Title + thumbnail overrides** are overlays. When a kid views a
  video, `KidLibraryService.apply_overrides()` patches the `title`
  field on the returned `Video` objects in-place for that kid's
  request. Custom thumbnails are served by the `/media/thumb/{id}`
  route, which checks for a file at
  `thumbnails/profiles/{profile_id}/{video_id}.*` before falling
  back to the canonical thumbnail. Sibling kids and the parent still
  see the canonical title/thumbnail.
- **Tags** are personal per kid. `tags` is a shared registry (so
  "science" typed twice doesn't create two rows), but
  `profile_video_tags` carries `profile_id` in its primary key, so
  each kid has an independent tagging system and their own tag
  cloud. The cloud query joins through to `videos` to respect
  channel visibility.
- **Channel bookmarks** let a kid add any visible video (parent
  downloads, uploads, other kids' shared content) into one of their
  own channels, YouTube-playlist-style. Nothing about the video
  changes — the bookmark is just a row in `profile_channel_videos`.
  Kid-owned channels additionally carry banner/icon/color art
  served by new `/media/channel/{id}/banner` and `/icon` routes.

---

## Theming System

Theming is primarily **CSS-only**: each theme ships a single stylesheet
at `app/static/kid/theme-<name>.css` that overrides CSS custom properties
declared in `app/static/kid/style.css`. `base/kid/base.html` links the
theme stylesheet conditionally:

```html
<link rel="stylesheet" href="/static/kid/style.css">
{% if viewer and viewer.theme and viewer.theme != 'base' %}
<link rel="stylesheet" href="/static/kid/theme-{{ viewer.theme }}.css">
{% endif %}
```

**Template overrides** are still supported for structural changes: drop
a file under `app/templates/themes/<name>/kid/<template>.html` and it
will take precedence over `base/`. This is implemented via Jinja2's
`FileSystemLoader` with the theme directory placed ahead of the base
directory. As of this writing neither bundled theme ships any
structural overrides — both are pure CSS — but the mechanism is still
available for future themes that need it.

**Resolution order** for template `kid/home.html` when profile theme is `playful`:
1. `app/templates/themes/playful/kid/home.html` (if exists)
2. `app/templates/base/kid/home.html` (fallback)

```python
theme_dir = f"app/templates/themes/{viewer.theme}"
base_dir = "app/templates/base"
loader = FileSystemLoader([theme_dir, base_dir])
```

**CSS theming** uses CSS custom properties. The base stylesheet declares
fallback values; theme stylesheets override them:

```css
/* static/kid/style.css */
:root {
    --bg-color: #f5f5f5;
    --accent: #2a9d8f;
    --card-radius: 8px;
    --font-size-title: 14px;
}

/* static/kid/theme-playful.css */
:root {
    --bg-color: #fffbe6;
    --accent: #ff6b6b;
    --card-radius: 16px;
    --font-size-title: 18px;
}
```

**Important:** don't duplicate the whole `kid/base.html` just to inject
theme CSS variables — that silently forks the header/nav markup, which
has bitten us before. Keep structural markup in `base/kid/base.html`
and restrict theme overrides to either the static CSS file or
intentionally-forked structural templates.

**Note on old browsers:** CSS custom properties require iOS 9.3+. For older
devices, the base stylesheet hardcodes fallback values before the variable
declarations, so old browsers use the fallback and modern browsers use the
variable.

---

## Request Flow (example: kid watches a video)

```
Browser: GET /watch/dQw4w9WgXcQ
    │
    ▼
kid_watch/router.py
    │  viewer = Depends(get_viewer)        → ViewerContext(child, profile_id=2)
    │  content = Depends(get_content_service)
    │  events = Depends(get_event_service)
    │
    │  video = content.get_video_for_viewer("dQw4w9WgXcQ", viewer)
    │  events.log("page_view", viewer, video_id="dQw4w9WgXcQ")
    │
    │  return render(request, viewer, "kid/watch.html", {"video": video})
    │
    ▼
ContentService.get_video_for_viewer()
    │  video = self.video_repo.get("dQw4w9WgXcQ")
    │  if video is None or video.status != "active": raise VideoNotFound
    │  if viewer has channel restrictions:
    │      if video.channel_id not in viewer.allowed_channel_ids: raise NotAuthorized
    │  return video
    │
    ▼
VideoRepository.get("dQw4w9WgXcQ")
    │  SELECT * FROM videos WHERE video_id = ?
    │  return Video(...)
```

Every layer has a single responsibility. Adding a new check (e.g., "is this
video age-appropriate for this profile?") means adding one line in
`ContentService`, not touching routes or SQL.

---

## Adding a New Feature (contributor guide)

Example: adding a "favorites" feature where kids can star videos.

1. **Schema:** Create `app/db/migrations/NNNN_favorites.sql` with the
   `CREATE TABLE favorites (...)`. Also update `app/db/schema.sql` to
   reflect the new canonical shape (reference only). The migrator
   applies your file on the next startup; existing DBs pick it up
   automatically without a wipe.

2. **Model:** Create `app/models/favorite.py` with a `Favorite` dataclass.

3. **Repository:** Create `app/repositories/favorite_repo.py` with
   `FavoriteRepository` (add, remove, list_for_profile).

4. **Service:** Either add to `ContentService` or create
   `app/services/favorites.py` if the logic is substantial.

5. **Feature:** Create `app/features/kid_favorites/router.py` with routes
   `POST /favorites/add`, `POST /favorites/remove`, `GET /favorites`.

6. **Wire:** In `app/main.py`, add `app.include_router(kid_favorites.router)`.
   In `app/dependencies.py`, add `get_favorite_repo()` and update the service
   dependency if needed.

7. **Templates:** Add `app/templates/base/kid/favorites.html`. If needed, add
   theme overrides.

8. **Tests:** Add `tests/repositories/test_favorite_repo.py` and
   `tests/features/test_kid_favorites.py`.

Total files touched outside the new feature: `schema.sql`, `main.py`,
`dependencies.py`. Everything else is new files in new directories.

---

## Testing Strategy

- **Repository tests:** Use an in-memory SQLite database with the full
  schema applied via `conftest.py`. Test query methods directly. Fast,
  no mocking.
- **Service tests:** Use real repositories backed by in-memory SQLite
  (simpler and more realistic than mocking). Test business logic through
  the service API.
- **Route tests:** Use FastAPI's `TestClient` with dependency overrides
  to inject in-memory DB. Test HTTP status codes, redirects, and auth
  enforcement.
- **No end-to-end browser tests in the MVP.** We may add Playwright tests
  later.

---

## Video Backend Abstraction

All interaction with YouTube (metadata fetching, video downloading) goes
through the `VideoBackend` abstract interface (`app/backends/base.py`).
The only implementation today is `YtdlpBackend` (`app/backends/ytdlp.py`).

**Why:** yt-dlp is actively maintained but could be abandoned, forked, or
blocked by YouTube. By isolating all yt-dlp-specific code behind an interface,
we can swap to a fork or a completely different tool by implementing one class
and changing one line in `app/dependencies.py` (`get_backend()`). No service,
route, or template code changes.

The interface requires four methods, all platform-agnostic (they
take full URLs, not IDs, so yt-dlp's multi-site extractors all
work uniformly):
- `fetch_video_info(url)` → metadata for one video (any source)
- `fetch_channel_videos(url, max_results)` → list of videos from a channel
- `fetch_playlist(url, max_results)` → list of videos from a playlist
- `download_video(url, output_dir, resolution, subtitle_langs)` → download to disk

The returned `VideoMetadata` always carries `extractor` (from
yt-dlp's `extractor_key` field, lowercased) and `original_url`
(from `webpage_url`) so the service and template layers can make
per-platform decisions (embed URL building, the "original" link,
the composite storage `video_id`) without re-running yt-dlp.

Services call these methods through `VideoSourceService` and
`StorageService`, which add URL parsing, file management, and
download locking on top.

---

## Crash Recovery & Data Integrity

The server must survive unclean shutdowns (RPi power loss, laptop lid close,
killed process) without data corruption or stuck state.

### Protections

1. **SQLite WAL mode.** Write-ahead logging is crash-safe by design. A power
   loss mid-transaction rolls back cleanly on next open. Enabled in
   `app/db/connection.py`.

2. **Atomic config writes.** `Config.save()` writes to a `.tmp` file, calls
   `fsync()`, then atomically renames over the target. If power dies mid-write,
   the old config file survives intact.

3. **Startup crash recovery** (`app/db/schema.py: recover_from_crash()`).
   Runs on every server start, is idempotent:
   - Videos stuck in `download_status='downloading'` → reset to `'pending'`
   - `.part` files left by interrupted yt-dlp → deleted
   - Videos marked `'ready'` but file missing from disk → reset to `'pending'`

4. **Automatic pending resume** (`ContentService.resume_pending_downloads()`).
   Right after `recover_from_crash`, `app/main.py` builds a one-shot
   ContentService and re-queues every `'pending'` cache-mode video. Each
   download runs in its own daemon thread with its own DB connection,
   so the startup caller returns immediately after queuing. Without this
   step, cache-mode videos orphaned by a kill-9 would stay pending until
   a parent reloaded the Add flow and lazily re-triggered them.

5. **Download locking.** Per-video `threading.Lock` in `StorageService`
   prevents duplicate concurrent downloads of the same video. Locks are
   in-memory and non-persistent — they reset naturally on restart.

6. **CSRF + per-request correlation ID.** All state-changing form posts
   are protected by signed per-session CSRF tokens (`app/middleware/csrf.py`)
   with SameSite=Strict session cookies; every request gets a correlation
   ID (`app/middleware/request_id.py`) that is bound into log records so a
   crash traceback can be tied back to the originating request. Request
   bodies are size-capped upstream of route handlers
   (`app/middleware/body_size.py`) so a runaway upload can't OOM the box.

7. **Backups.** `scripts/backup.sh` + `scripts/restore.sh` ship with a
   systemd timer template; see [`backup.md`](backup.md). Restores are the
   first-class recovery path if WAL-level protections aren't enough.

8. **Cache lifecycle.** Cache-mode videos (`storage_mode='cache'`) set
   `cached_at = utcnow()` when the download completes. A background
   asyncio task in `app/main.py` lifespan runs
   `StorageService.evict_expired` every
   `config.storage.cache_cleanup_interval_minutes` (default 60). The
   sweep deletes files on disk, transitions the row to
   `download_status = 'evicted'`, and clears `cached_at` /
   `cache_expires_at` / `file_size`. Eligibility is defined in
   `VideoRepository.list_expired_cache`:
   `storage_mode='cache'` AND `keep_forever=0` AND `download_status='ready'`
   AND `cached_at < datetime('now', '-N days')`. The sweep is a no-op
   when `cache_days <= 0` (keep-forever mode) or when the interval is
   `0` (disabled). When a kid tries to watch an evicted video,
   `ContentService.try_rehydrate_evicted` flips it back to `pending`
   and queues a re-download; the kid sees a `kid/preparing.html` page
   that auto-refreshes. Uploaded videos (`storage_mode='uploaded'`)
   are never touched. Per-video library-mode (`keep_forever=1`) pins
   favourites from the parent edit form.

9. **Graceful shutdown timeout.** `uvicorn.run(..., timeout_graceful_shutdown=3)`
   in `run.py` caps how long Ctrl+C waits for in-flight HTTP connections
   to drain. Browsers holding a `<video>` keep-alive socket on
   `/media/video/...` used to block graceful shutdown indefinitely — the
   3-second cap closes them cleanly on a single Ctrl+C.

### Shared curation (export / import)

Parents share curated channels by file, not by network sync. Export
lives at `GET /parent/channels/{id}/export?format=ytc|txt|pdf` in
`app/features/parent_sharing/router.py`; import at
`GET`/`POST /parent/channels/import`. All encode/decode/render logic
lives in `app/services/sharing.py` and is deliberately pure (no DB
writes, no HTTP — repositories are passed in for reads only).

- **.ytc format** is JSON with a mandatory `"schema": "curatables.ytc/1"`
  top-level field. Decode is strict on schema version (refuses any
  other value with a clear error) but lenient on unknown keys within
  v1 — so a v1.1 can add fields without breaking older clients.
  Bumping to `curatables.ytc/2` is the escape hatch for breaking
  changes.
- **Security posture: re-fetch, don't trust.** The importer calls
  `ContentService.fetch_previews_for_urls` for every URL, which
  re-hits the source via yt-dlp. Titles/descriptions in the file are
  hints only — never used to populate the parent's review page. This
  closes the obvious injection surface.
- **Import is review-gated.** After the batch fetch, the router
  renders the same `parent/content_preview.html` template that the
  URL-based add flow uses. The existing "I have already fully
  watched this video" checkbox gate (`parent_content/router.py:133`)
  still applies unchanged.
- **PDF export is warn-and-degrade.** `reportlab` is optional; if
  missing, the PDF route returns a clean 503 pointing at the install
  command, and the `.ytc` + `.txt` paths keep working.
- **No new DB tables.** Import is orchestration over the existing
  `videos`/`sources`/`channels` write paths. Subscriptions (deferred
  to Future) are where an `imports` ledger would live.

### Concurrency

Two tabs — or six — from the same kid writing to the DB at once is a
real workload (reactions, comments, tag edits, page views from the
watch page all fire independently). The story:

- **SQLite WAL** lets any number of readers proceed concurrently; writers
  serialize for microseconds, not whole requests. The
  `check_same_thread=False` connection flag lets background download
  threads share the file safely with request threads.
- **Per-request connections** (via the `get_db` FastAPI dependency) mean
  no connection state is shared across requests. A request that
  deadlocks or errors out can't poison other requests.
- **Race-free upserts.** Three places in the codebase do concurrent
  upsert: reactions (`INSERT ... ON CONFLICT DO UPDATE`), per-kid
  bookmarks (`INSERT OR IGNORE`), and tag/override upserts (`INSERT OR
  IGNORE`-then-`SELECT` for tags; try-INSERT-fall-back-to-UPDATE for
  overrides and channels). All four patterns are safe against two tabs
  writing the same key at the same instant — no tab ever sees a 500.

### Startup sequence

`app/main.py:create_app()` runs these in order before the HTTP listener
starts:

1. `recover_from_crash(conn, data_dir)` — see above.
2. `ContentService.resume_pending_downloads()` — kick pending videos.
3. `UploadService.sweep_abandoned(ttl_hours=24)` — drop old `.tmp/` chunks.
4. mDNS advertiser starts (inside the lifespan context manager).

Each step logs a one-line summary so a post-restart tail of the log
file shows exactly what was repaired.

### Design rule

No operation should leave the system in a state that requires manual
intervention to fix. If the server crashes at any point, the next startup
must recover automatically.

---

## What This Architecture Does NOT Include (Yet)

These are future concerns that the architecture can accommodate but that we
will not build prematurely:

- **Plugin system.** If needed, services can be made into interfaces with
  swappable implementations. The DI layer already supports this.
- **WebSocket push.** Can be added as a new feature directory without changing
  existing code.
- **LLM integration.** Will be a service (`app/services/recommender.py`) that
  reads from `EventRepository` and writes suggestions. No architectural changes
  needed.
- **Shared curation export/import.** A feature directory
  (`app/features/sharing/`) with its own schemas for the `.ytc` format.
- **Background tasks.** FastAPI's `BackgroundTasks` or a simple thread pool
  for cache cleanup, thumbnail downloads, and channel sync.

---

## Inspired By

- **Navidrome** — clean architecture with model/persistence/core/adapter layers
- **Jellyfin** — interface-based DI, per-user display preferences, plugin contracts
- **Invidious** — YouTube proxy architecture, domain-per-directory structure
- **AdGuard Home** — one-package-per-concern, thorough contributor documentation
- **FastAPI full-stack template** — repository pattern, dependency injection,
  feature-based routing
