"""0016_wishlist_auto_add: the channel a kid's requests go straight into
(when their YouTube channel is on the parent's allowed list), and which
requests were added that way. Idempotent."""

import sqlite3


def _add(conn: sqlite3.Connection, table: str, name: str, decl: str) -> None:
    cols = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if name not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def up(conn: sqlite3.Connection) -> None:
    _add(conn, "profiles", "auto_add_channel_id",
         "INTEGER REFERENCES channels(id) ON DELETE SET NULL")
    _add(conn, "wishlist_requests", "auto_added", "INTEGER NOT NULL DEFAULT 0")
