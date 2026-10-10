"""0013_wishlist_details — keep everything YouTube said about a requested
video (so the parent's review needs no second fetch) and the kid's own
title, description and channel for it. Idempotent."""

import sqlite3

_COLUMNS = {
    "original_title": "TEXT NOT NULL DEFAULT ''",
    "description": "TEXT NOT NULL DEFAULT ''",
    "original_description": "TEXT NOT NULL DEFAULT ''",
    "upload_date": "TEXT NOT NULL DEFAULT ''",
    "view_count": "INTEGER NOT NULL DEFAULT 0",
    "target_channel_id": "INTEGER REFERENCES channels(id) ON DELETE SET NULL",
}


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(wishlist_requests)").fetchall()}
    for name, decl in _COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE wishlist_requests ADD COLUMN {name} {decl}")
    conn.execute("UPDATE wishlist_requests SET original_title = title WHERE original_title = ''")
