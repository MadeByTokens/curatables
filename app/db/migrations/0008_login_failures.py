"""0008_login_failures: failed parent logins, so the login throttle
survives a restart. Idempotent."""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS login_failures ("
        " client TEXT NOT NULL,"
        " at REAL NOT NULL)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_login_failures_at ON login_failures(at)")
