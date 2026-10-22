"""0026_videos_downloaded_at: when a video's files were last downloaded
again after being deleted (freed). Empty means it still has its first
download, from when it was added. Newest and Oldest go by it.
Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
    if "downloaded_at" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN downloaded_at TEXT")
