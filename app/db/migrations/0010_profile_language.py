"""0010_profile_language: the language of each kid's screens. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "profiles" not in tables:
        return
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "language" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN language TEXT NOT NULL DEFAULT 'en'")
