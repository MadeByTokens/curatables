"""0002_bootstrap_existing: add columns that databases created before
the migrator may lack.

SQLite has no ADD COLUMN IF NOT EXISTS, so each column is probed via
PRAGMA table_info first. On a database built by 0001 this is a no-op.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # (table, column, column declaration)
    expected = [
        ("channels", "banner_filename", "TEXT"),
        ("channels", "icon_filename", "TEXT"),
        ("channels", "color", "TEXT DEFAULT '#2a9d8f'"),
        ("profile_video_overrides", "description", "TEXT"),
    ]

    for table, column, decl in expected:
        cols = {row["name"] for row in
                conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column in cols:
            continue
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
