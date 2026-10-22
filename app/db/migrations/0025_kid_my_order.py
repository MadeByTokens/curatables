"""0025_kid_my_order: a kid's own order for a channel's videos (My
order): the saved order, the one before the last save (Undo save) and
the sort picked then, and a draft being arranged with its undo steps.
Orders are JSON lists of video ids. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profile_channel_order ("
        " profile_id   INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,"
        " channel_id   INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,"
        " saved        TEXT,"
        " saved_before TEXT,"
        " sort_before  TEXT,"
        " draft        TEXT,"
        " undo         TEXT NOT NULL DEFAULT '[]',"
        " PRIMARY KEY (profile_id, channel_id))")
