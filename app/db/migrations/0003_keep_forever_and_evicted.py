"""0003_keep_forever_and_evicted: add videos.keep_forever, which exempts
a video from cache eviction.

The accompanying download_status value 'evicted' needs no schema change
because that column has no CHECK constraint. Idempotent.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(videos)").fetchall()}
    if "keep_forever" not in cols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN keep_forever INTEGER NOT NULL DEFAULT 0"
        )
