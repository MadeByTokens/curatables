"""0003_keep_forever_and_evicted — add per-video library-mode flag.

Adds `videos.keep_forever` (INTEGER, 0/1). When set, the cache
eviction sweeper in `StorageService.evict_expired` skips the video,
so parents can pin favourite content permanently on disk regardless
of `cache_days`.

The new `download_status = 'evicted'` value introduced alongside this
column is a string value, not a schema constraint (the column has no
CHECK), so no column change is needed for it.

Idempotent: probes PRAGMA table_info and only ADDs if missing.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(videos)").fetchall()}
    if "keep_forever" not in cols:
        conn.execute(
            "ALTER TABLE videos ADD COLUMN keep_forever INTEGER NOT NULL DEFAULT 0"
        )
