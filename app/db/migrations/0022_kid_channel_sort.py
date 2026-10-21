"""0022_kid_channel_sort: the order each kid picked for a channel's videos
(oldest first, A to Z or Z to A by the titles that kid sees). No row
means newest first. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS profile_channel_sort ("
        " profile_id INTEGER NOT NULL REFERENCES profiles(id) ON DELETE CASCADE,"
        " channel_id INTEGER NOT NULL REFERENCES channels(id) ON DELETE CASCADE,"
        " sort       TEXT NOT NULL,"
        " PRIMARY KEY (profile_id, channel_id))")
