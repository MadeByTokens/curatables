-- curatables database schema: a reference snapshot, never executed.
--
-- The runtime schema comes from app/db/migrations/, applied in order by
-- app/db/migrator.py. When adding a migration, update this file to the
-- resulting shape (columns in the order the migrations create them).

CREATE TABLE IF NOT EXISTS channels (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL UNIQUE,
    description      TEXT DEFAULT '',
    position         INTEGER DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (datetime('now')),
    owner_profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    banner_filename  TEXT,
    icon_filename    TEXT,
    color            TEXT DEFAULT '#2a9d8f'
);

CREATE TABLE IF NOT EXISTS sources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source_type   TEXT NOT NULL CHECK(source_type IN ('channel', 'playlist', 'video')),
    extractor     TEXT NOT NULL DEFAULT 'youtube',
    external_id   TEXT NOT NULL,
    title         TEXT NOT NULL,
    description   TEXT DEFAULT '',
    url           TEXT NOT NULL,
    auto_sync     INTEGER DEFAULT 0,
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    status        TEXT NOT NULL DEFAULT 'active',
    metadata_json TEXT DEFAULT '{}',
    UNIQUE(extractor, external_id)
);

CREATE TABLE IF NOT EXISTS videos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id        TEXT NOT NULL UNIQUE,
    extractor       TEXT NOT NULL DEFAULT 'youtube',
    original_url    TEXT NOT NULL DEFAULT '',
    source_id       INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    channel_id      INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    title           TEXT NOT NULL,
    original_title  TEXT NOT NULL,
    channel_name    TEXT DEFAULT '',
    description     TEXT DEFAULT '',
    duration        INTEGER DEFAULT 0,
    upload_date     TEXT DEFAULT '',
    view_count      INTEGER DEFAULT 0,
    thumbnail_url   TEXT DEFAULT '',
    thumbnail_type  TEXT DEFAULT 'original',
    status          TEXT NOT NULL DEFAULT 'active',
    -- 'pending' | 'downloading' | 'ready' | 'error' | 'evicted'
    -- ('evicted': the cache sweeper deleted the files; the row stays so
    -- the video can be downloaded again on demand).
    download_status TEXT NOT NULL DEFAULT 'pending',
    download_error  TEXT DEFAULT '',
    storage_mode    TEXT DEFAULT 'cache',
    resolution      TEXT DEFAULT '720p',
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    cached_at       TEXT,
    cache_expires_at TEXT,
    file_size       INTEGER DEFAULT 0,
    -- keep_forever=1 exempts the video from cache auto-eviction.
    keep_forever    INTEGER NOT NULL DEFAULT 0,
    -- last_played_at drives least-recently-watched cache eviction.
    last_played_at  TEXT,
    play_count      INTEGER NOT NULL DEFAULT 0,  -- kid plays, kept for good
    -- Imported from a drive: where it came from, and the file it was.
    origin          TEXT NOT NULL DEFAULT '',   -- movie | youtube | web | home
    origin_ref      TEXT NOT NULL DEFAULT '',   -- Wikidata / TMDB / YouTube id
    source_path     TEXT NOT NULL DEFAULT '',
    -- season * 1000 + episode: orders a show's episodes in its channel.
    episode_key     INTEGER
);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    pin         TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    avatar      TEXT DEFAULT 'default',
    theme       TEXT DEFAULT 'base',
    search_mode TEXT DEFAULT 'disabled',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    -- channels_restricted=1: only profile_channels rows (+ owned) are visible.
    channels_restricted INTEGER NOT NULL DEFAULT 0,
    -- Per-kid feature switches (1 = on).
    allow_comments  INTEGER NOT NULL DEFAULT 1,
    allow_reactions INTEGER NOT NULL DEFAULT 1,
    allow_tags      INTEGER NOT NULL DEFAULT 1,
    allow_customize INTEGER NOT NULL DEFAULT 1,
    allow_upload    INTEGER NOT NULL DEFAULT 1,
    auto_subtitles  INTEGER NOT NULL DEFAULT 1,
    pin_style   TEXT NOT NULL DEFAULT 'digits',  -- 'digits' | 'pictures'
    language    TEXT NOT NULL DEFAULT 'en',      -- kid screens, see app/i18n
    allow_wishlist  INTEGER NOT NULL DEFAULT 0,
    -- Requests from trusted YouTube channels go straight into this channel
    -- when auto_add_mode is 'on'.
    auto_add_channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL,
    auto_add_mode TEXT NOT NULL DEFAULT 'family',  -- 'family' | 'off' | 'on'
    -- Find new shows only trusted YouTube channels: 'family' | 'on' | 'off'.
    trusted_only TEXT NOT NULL DEFAULT 'family',
    allow_download  INTEGER NOT NULL DEFAULT 1,
    allow_hide      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS profile_channels (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    PRIMARY KEY (profile_id, channel_id)
);

CREATE TABLE IF NOT EXISTS reactions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL,
    emoji      TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(profile_id, video_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id   TEXT NOT NULL,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE CASCADE,
    parent_comment_id INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    is_parent_user INTEGER DEFAULT 0,
    body       TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    edited_at  TEXT,                        -- the author changed it
    deleted    INTEGER NOT NULL DEFAULT 0   -- the author deleted it; others replied
);

CREATE TABLE IF NOT EXISTS events (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL,
    video_id   TEXT,
    timestamp  TEXT NOT NULL DEFAULT (datetime('now')),
    data_json  TEXT DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS profile_video_overrides (
    profile_id       INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id         TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    title            TEXT,
    description      TEXT,
    has_custom_thumb INTEGER DEFAULT 0,
    PRIMARY KEY (profile_id, video_id)
);

-- A kid hid the video from their own lists; reviewed = 1 once the
-- parent chose to keep it hidden.
CREATE TABLE IF NOT EXISTS profile_hidden_videos (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    hidden_at  TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (profile_id, video_id)
);

CREATE TABLE IF NOT EXISTS tags (
    id   INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS profile_video_tags (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    tag_id     INTEGER NOT NULL REFERENCES tags(id) ON DELETE CASCADE,
    PRIMARY KEY (profile_id, video_id, tag_id)
);

CREATE TABLE IF NOT EXISTS profile_channel_videos (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,
    video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,
    position   INTEGER DEFAULT 0,
    added_at   TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (profile_id, channel_id, video_id)
);

CREATE TABLE IF NOT EXISTS wishlist_requests (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id    INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    video_id      TEXT NOT NULL,              -- composite id, as in videos
    extractor     TEXT NOT NULL,
    raw_id        TEXT NOT NULL,
    title         TEXT NOT NULL DEFAULT '',   -- the kid's title (YouTube's until changed)
    channel       TEXT NOT NULL DEFAULT '',   -- the YouTube channel's name
    duration      INTEGER NOT NULL DEFAULT 0,
    url           TEXT NOT NULL,
    thumbnail_url TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    decided_at    TEXT,
    original_title TEXT NOT NULL DEFAULT '',
    description   TEXT NOT NULL DEFAULT '',   -- the kid's description
    original_description TEXT NOT NULL DEFAULT '',
    upload_date   TEXT NOT NULL DEFAULT '',
    view_count    INTEGER NOT NULL DEFAULT 0,
    target_channel_id INTEGER REFERENCES channels(id) ON DELETE SET NULL,  -- the kid's pick
    suggested_channel TEXT NOT NULL DEFAULT '',  -- a new channel's name the kid suggests
    auto_added    INTEGER NOT NULL DEFAULT 0,   -- added without the parent's review
    youtube_channel_id TEXT NOT NULL DEFAULT '',  -- UC..., '' when not known
    auto_added_at TEXT,                         -- counts a kid's automatic adds per day
    held_by_limit INTEGER NOT NULL DEFAULT 0,   -- waited: that day's allowance was used up
    UNIQUE (profile_id, video_id)
);

-- YouTube channels the parent trusts for every kid, by channel ID.
CREATE TABLE IF NOT EXISTS trusted_youtube_channels (
    channel_id TEXT PRIMARY KEY,             -- UC...
    name       TEXT NOT NULL DEFAULT '',
    added_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Per kid: trusted = 1 adds a channel for this kid only; trusted = 0
-- switches a family channel off for this kid.
CREATE TABLE IF NOT EXISTS profile_youtube_channels (
    profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,
    channel_id TEXT NOT NULL,
    name       TEXT NOT NULL DEFAULT '',
    trusted    INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (profile_id, channel_id)
);

-- One row per file imported from a drive: the wizard's draft, then the
-- background job's progress, then its outcome.
CREATE TABLE IF NOT EXISTS import_jobs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    batch         TEXT NOT NULL,
    source_path   TEXT NOT NULL,
    source_size   INTEGER NOT NULL DEFAULT 0,
    source_mtime  REAL NOT NULL DEFAULT 0,
    details       TEXT NOT NULL DEFAULT '{}',   -- JSON: what the parent chose
    picture       BLOB,                         -- the chosen cover picture
    mode          TEXT NOT NULL DEFAULT 'copy', -- copy | move
    keep_original INTEGER NOT NULL DEFAULT 1,
    -- draft | queued | working | cancelling | cancelled | done | failed
    state         TEXT NOT NULL DEFAULT 'draft',
    phase         TEXT NOT NULL DEFAULT '',     -- copy | hash | convert | subtitles | finish
    done          REAL NOT NULL DEFAULT 0,
    total         REAL NOT NULL DEFAULT 0,
    speed         REAL,
    eta_seconds   REAL,
    stalled_for   REAL NOT NULL DEFAULT 0,
    error         TEXT NOT NULL DEFAULT '',
    video_id      TEXT NOT NULL DEFAULT '',
    kind          TEXT NOT NULL DEFAULT '',     -- conversion kind, for later estimates
    media_seconds REAL NOT NULL DEFAULT 0,
    wall_seconds  REAL NOT NULL DEFAULT 0,
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at   TEXT
);

CREATE TABLE IF NOT EXISTS login_failures (
    client TEXT NOT NULL,
    at     REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source_id);
CREATE INDEX IF NOT EXISTS idx_channels_owner ON channels(owner_profile_id);
CREATE INDEX IF NOT EXISTS idx_events_profile ON events(profile_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_video ON events(video_id);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_profile_channels ON profile_channels(profile_id);
CREATE INDEX IF NOT EXISTS idx_reactions_video ON reactions(video_id);
CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_comment_id);
CREATE INDEX IF NOT EXISTS idx_pvt_profile ON profile_video_tags(profile_id);
CREATE INDEX IF NOT EXISTS idx_pvt_tag ON profile_video_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_pcv_channel ON profile_channel_videos(profile_id, channel_id);
CREATE INDEX IF NOT EXISTS idx_login_failures_at ON login_failures(at);
CREATE INDEX IF NOT EXISTS idx_wishlist_status ON wishlist_requests(status);
CREATE INDEX IF NOT EXISTS idx_import_jobs_state ON import_jobs(state);
CREATE INDEX IF NOT EXISTS idx_import_jobs_batch ON import_jobs(batch);
CREATE INDEX IF NOT EXISTS idx_videos_source_path ON videos(source_path);
