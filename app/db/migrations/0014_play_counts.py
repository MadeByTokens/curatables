"""0014_play_counts: count how often each video was played, for good.

`events` keeps only events_retention_days of history, so the parent's
"least watched" list needs its own counter. Backfilled from the
`video_play` events still kept. Also indexes events by type and time,
which every stats query filters on. Idempotent.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "videos" in tables:
        cols = {row["name"] for row in
                conn.execute("PRAGMA table_info(videos)").fetchall()}
        if "play_count" not in cols:
            conn.execute("ALTER TABLE videos ADD COLUMN play_count INTEGER NOT NULL DEFAULT 0")
            if "events" in tables:
                conn.execute(
                    "UPDATE videos SET play_count = ("
                    "  SELECT COUNT(*) FROM events e"
                    "  WHERE e.video_id = videos.video_id AND e.event_type = 'video_play')")
    if "events" in tables:
        conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time "
                     "ON events(event_type, timestamp)")
