"""0017_comment_edits: kids edit and delete their own comments.
`edited_at` marks a changed comment; `deleted` keeps the placeholder of
one whose thread has replies by others. Idempotent."""

import sqlite3

_COLUMNS = {
    "edited_at": "TEXT",
    "deleted": "INTEGER NOT NULL DEFAULT 0",
}


def up(conn: sqlite3.Connection) -> None:
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(comments)").fetchall()}
    for name, decl in _COLUMNS.items():
        if name not in cols:
            conn.execute(f"ALTER TABLE comments ADD COLUMN {name} {decl}")
