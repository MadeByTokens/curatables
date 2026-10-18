"""0015_wishlist_suggested_channel: the name of a new channel a kid
suggests for a requested video (a kid who can't make channels).
Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(wishlist_requests)").fetchall()}
    if "suggested_channel" not in cols:
        conn.execute("ALTER TABLE wishlist_requests "
                     "ADD COLUMN suggested_channel TEXT NOT NULL DEFAULT ''")
