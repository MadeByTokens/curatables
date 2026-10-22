"""0023_kid_channel_looks: a kid's own name and picture for a channel,
seen only by that kid (an emoji, or a picture file kept beside the
channel's art). Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profile_channel_looks ("
        " profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,"
        " channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,"
        " name       TEXT NOT NULL DEFAULT '',"
        " emoji      TEXT NOT NULL DEFAULT '',"
        " picture    TEXT NOT NULL DEFAULT '',"
        " PRIMARY KEY (profile_id, channel_id))")
