"""0009_profile_pin_style — a profile's PIN is typed as digits or picked
as three pictures. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "profiles" not in tables:
        return
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "pin_style" not in cols:
        conn.execute(
            "ALTER TABLE profiles ADD COLUMN pin_style TEXT NOT NULL DEFAULT 'digits'")
