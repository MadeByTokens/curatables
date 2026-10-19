"""0018_trusted_youtube_channels: YouTube channels the parent trusts, by
channel ID, for the whole family and per kid. A kid's row either adds a
channel for that kid (trusted = 1) or switches a family one off for them
(trusted = 0). Each kid follows the family's "only trusted channels"
and "add without asking" settings unless set otherwise. Requests keep
the video's YouTube channel ID. Idempotent."""

import sqlite3


def _add(conn: sqlite3.Connection, table: str, name: str, decl: str) -> bool:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name in cols:
        return False
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
    return True


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS trusted_youtube_channels ("
        "  channel_id TEXT PRIMARY KEY, "
        "  name TEXT NOT NULL DEFAULT '', "
        "  added_at TEXT NOT NULL DEFAULT (datetime('now')))")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profile_youtube_channels ("
        "  profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE, "
        "  channel_id TEXT NOT NULL, "
        "  name TEXT NOT NULL DEFAULT '', "
        "  trusted INTEGER NOT NULL DEFAULT 1, "
        "  PRIMARY KEY (profile_id, channel_id))")
    _add(conn, "profiles", "trusted_only", "TEXT NOT NULL DEFAULT 'family'")
    if _add(conn, "profiles", "auto_add_mode", "TEXT NOT NULL DEFAULT 'family'"):
        # A kid whose requests already went into a channel keeps that.
        conn.execute("UPDATE profiles SET auto_add_mode = 'on' "
                     "WHERE auto_add_channel_id IS NOT NULL")
    _add(conn, "wishlist_requests", "youtube_channel_id", "TEXT NOT NULL DEFAULT ''")
