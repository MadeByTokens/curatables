"""0005_hash_profile_pins: replace plaintext kid PINs with PBKDF2 hashes.

Rows whose `profiles.pin` is non-empty and not already in the
`pbkdf2$<salt>$<hash>` form are rewritten in place. Idempotent.
"""

import sqlite3

from app.services.auth import hash_pin, is_hashed_pin


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "profiles" not in tables:
        return
    rows = conn.execute(
        "SELECT id, pin FROM profiles WHERE pin IS NOT NULL AND pin != ''"
    ).fetchall()
    for row in rows:
        if not is_hashed_pin(row["pin"]):
            conn.execute("UPDATE profiles SET pin = ? WHERE id = ?",
                         (hash_pin(row["pin"]), row["id"]))
