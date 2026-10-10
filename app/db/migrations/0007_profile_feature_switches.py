"""0007_profile_feature_switches: per-kid on/off switches for kid features.

Adds INTEGER 0/1 columns to `profiles`: allow_comments, allow_reactions,
allow_tags, allow_customize, allow_upload, auto_subtitles. Existing
profiles keep every feature on (their current behaviour); the parent
form starts new profiles in a simpler "watch + react" setup.
Idempotent.
"""

import sqlite3

COLUMNS = (
    "allow_comments", "allow_reactions", "allow_tags",
    "allow_customize", "allow_upload", "auto_subtitles",
)


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "profiles" not in tables:
        return
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(profiles)").fetchall()}
    for col in COLUMNS:
        if col not in cols:
            conn.execute(
                f"ALTER TABLE profiles ADD COLUMN {col} INTEGER NOT NULL DEFAULT 1")
