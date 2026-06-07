-- curatables database schema
-- This is the single source of truth for the database structure.
-- All tables are designed for the full PRD scope.

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
    download_status TEXT NOT NULL DEFAULT 'pending',
    download_error  TEXT DEFAULT '',
    storage_mode    TEXT DEFAULT 'cache',
    resolution      TEXT DEFAULT '720p',
    added_at        TEXT NOT NULL DEFAULT (datetime('now')),
    cached_at       TEXT,
    cache_expires_at TEXT,
    file_size       INTEGER DEFAULT 0
);

CREATE TABLE IF NOT EXISTS profiles (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    pin         TEXT DEFAULT '',
    display_name TEXT DEFAULT '',
    avatar      TEXT DEFAULT 'default',
    theme       TEXT DEFAULT 'base',
    search_mode TEXT DEFAULT 'disabled',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
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
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
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

CREATE INDEX IF NOT EXISTS idx_videos_status ON videos(status);
CREATE INDEX IF NOT EXISTS idx_videos_channel ON videos(channel_id);
CREATE INDEX IF NOT EXISTS idx_videos_source ON videos(source_id);
CREATE INDEX IF NOT EXISTS idx_channels_owner ON channels(owner_profile_id);
CREATE INDEX IF NOT EXISTS idx_events_profile ON events(profile_id);
CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp);
CREATE INDEX IF NOT EXISTS idx_events_video ON events(video_id);
CREATE INDEX IF NOT EXISTS idx_profile_channels ON profile_channels(profile_id);
CREATE INDEX IF NOT EXISTS idx_reactions_video ON reactions(video_id);
CREATE INDEX IF NOT EXISTS idx_comments_video ON comments(video_id);
CREATE INDEX IF NOT EXISTS idx_comments_parent ON comments(parent_comment_id);
CREATE INDEX IF NOT EXISTS idx_pvt_profile ON profile_video_tags(profile_id);
CREATE INDEX IF NOT EXISTS idx_pvt_tag ON profile_video_tags(tag_id);
CREATE INDEX IF NOT EXISTS idx_pcv_channel ON profile_channel_videos(profile_id, channel_id);
