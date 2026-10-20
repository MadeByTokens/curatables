"""0019_wishlist_auto_added_at: when a request went into the library by
itself, so the box can count a kid's automatic adds per day, and whether
one waited only because that day's allowance was used up. Idempotent."""

import sqlite3

_COLUMNS = {
    "auto_added_at": "TEXT",
    "held_by_limit": "INTEGER NOT NULL DEFAULT 0",
}


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(wishlist_requests)").fetchall()}
    for name, decl in _COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE wishlist_requests ADD COLUMN {name} {decl}")
