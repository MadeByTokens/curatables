"""0011_wishlist: kids ask for YouTube videos, a parent approves or
rejects. Adds the per-profile allow_wishlist switch (off for every
existing profile) and the wishlist_requests table. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "profiles" in tables:
        cols = {row["name"] for row in
                conn.execute("PRAGMA table_info(profiles)").fetchall()}
        if "allow_wishlist" not in cols:
            conn.execute(
                "ALTER TABLE profiles ADD COLUMN allow_wishlist INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS wishlist_requests ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,"
        " video_id TEXT NOT NULL,"
        " extractor TEXT NOT NULL,"
        " raw_id TEXT NOT NULL,"
        " title TEXT NOT NULL DEFAULT '',"
        " channel TEXT NOT NULL DEFAULT '',"
        " duration INTEGER NOT NULL DEFAULT 0,"
        " url TEXT NOT NULL,"
        " thumbnail_url TEXT NOT NULL DEFAULT '',"
        " status TEXT NOT NULL DEFAULT 'pending',"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " decided_at TEXT,"
        " UNIQUE (profile_id, video_id))"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_wishlist_status ON wishlist_requests(status)")
