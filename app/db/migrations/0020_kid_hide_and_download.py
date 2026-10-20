"""0020_kid_hide_and_download: a kid hides a video they don't like and the
parent is told; a kid saves a video onto their device. Adds the
per-profile allow_download and allow_hide switches (on for every existing
profile) and the profile_hidden_videos table. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    for name in ("allow_download", "allow_hide"):
        if name not in cols:
            conn.execute(f"ALTER TABLE profiles ADD COLUMN {name} INTEGER NOT NULL DEFAULT 1")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profile_hidden_videos ("
        " profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,"
        " video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,"
        " hidden_at  TEXT NOT NULL DEFAULT (datetime('now')),"
        " reviewed   INTEGER NOT NULL DEFAULT 0,"
        " PRIMARY KEY (profile_id, video_id))")
