"""0012_imports: import video files from a drive.

Adds where a video came from (origin, origin_ref, source_path), an
episode_key that orders a show's episodes inside its channel, and the
import_jobs table: one row per file, from the wizard's draft through
the background job's progress to its outcome. Idempotent."""

import sqlite3

_VIDEO_COLUMNS = {
    "origin": "TEXT NOT NULL DEFAULT ''",
    "origin_ref": "TEXT NOT NULL DEFAULT ''",
    "source_path": "TEXT NOT NULL DEFAULT ''",
    "episode_key": "INTEGER",
}


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(videos)").fetchall()}
    for name, decl in _VIDEO_COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE videos ADD COLUMN {name} {decl}")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS import_jobs ("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " batch TEXT NOT NULL,"
        " source_path TEXT NOT NULL,"
        " source_size INTEGER NOT NULL DEFAULT 0,"
        " source_mtime REAL NOT NULL DEFAULT 0,"
        " details TEXT NOT NULL DEFAULT '{}',"
        " picture BLOB,"
        " mode TEXT NOT NULL DEFAULT 'copy',"
        " keep_original INTEGER NOT NULL DEFAULT 1,"
        " state TEXT NOT NULL DEFAULT 'draft',"
        " phase TEXT NOT NULL DEFAULT '',"
        " done REAL NOT NULL DEFAULT 0,"
        " total REAL NOT NULL DEFAULT 0,"
        " speed REAL,"
        " eta_seconds REAL,"
        " stalled_for REAL NOT NULL DEFAULT 0,"
        " error TEXT NOT NULL DEFAULT '',"
        " video_id TEXT NOT NULL DEFAULT '',"
        " kind TEXT NOT NULL DEFAULT '',"
        " media_seconds REAL NOT NULL DEFAULT 0,"
        " wall_seconds REAL NOT NULL DEFAULT 0,"
        " created_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " updated_at TEXT NOT NULL DEFAULT (datetime('now')),"
        " finished_at TEXT)"
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_import_jobs_state ON import_jobs(state)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_import_jobs_batch ON import_jobs(batch)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_videos_source_path ON videos(source_path)")
