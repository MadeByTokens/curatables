"""0006_videos_last_played_at: remember when each video was last played.

Cache eviction keys on `COALESCE(last_played_at, cached_at)` so videos
that are watched often stay on disk. Backfilled from the newest
`video_play` event per video. Idempotent.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "videos" not in tables:
        return
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(videos)").fetchall()}
    if "last_played_at" not in cols:
        conn.execute("ALTER TABLE videos ADD COLUMN last_played_at TEXT")
    if "events" in tables:
        conn.execute(
            "UPDATE videos SET last_played_at = ("
            "  SELECT MAX(timestamp) FROM events e"
            "  WHERE e.video_id = videos.video_id AND e.event_type = 'video_play'"
            ") WHERE last_played_at IS NULL"
        )
