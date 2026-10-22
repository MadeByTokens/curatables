"""0024_kid_all_videos_sort: the order a kid picked for All videos
('oldest', 'az', 'za'); empty means newest first. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "all_sort" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN all_sort TEXT NOT NULL DEFAULT ''")
