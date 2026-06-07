"""0002_bootstrap_existing — add any columns that a pre-migrator DB
may be missing.

Purpose: users who upgrade across the introduction of the migration
system had their DB created by the old CREATE TABLE IF NOT EXISTS
path. That path silently skipped adding columns to tables that
already existed, so a DB from before can be missing any of the
columns we added during the pre-launch period:
  - channels.banner_filename
  - channels.icon_filename
  - channels.color
  - profile_video_overrides.description
  - profile_video_overrides.has_custom_thumb (may exist with wrong default)

SQLite lacks `ALTER TABLE ADD COLUMN IF NOT EXISTS`, so we probe
`PRAGMA table_info` and ADD each missing column. Idempotent.

Fresh installs (stamped at 0001) run this too; it's a no-op because
0001 already created all the columns.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # Pairs of (table, column, DDL-fragment-for-ADD-COLUMN).
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
