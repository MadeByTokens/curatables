"""0021_kid_home_screen: each kid arranges their own Home, like a tablet's
home screen: which video sits in which spot. `home_customized` stays 0
until the kid changes something; until then Home shows the newest
videos. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "home_customized" not in cols:
        conn.execute("ALTER TABLE profiles ADD COLUMN home_customized INTEGER NOT NULL DEFAULT 0")
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profile_home_slots ("
        " profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,"
        " position   INTEGER NOT NULL,"
        " video_id   TEXT NOT NULL REFERENCES videos(video_id) ON DELETE CASCADE,"
        " PRIMARY KEY (profile_id, position),"
        " UNIQUE (profile_id, video_id))")
