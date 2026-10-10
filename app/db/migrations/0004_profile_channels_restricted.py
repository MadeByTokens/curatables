"""0004_profile_channels_restricted: explicit channel-restriction flag.

Adds `profiles.channels_restricted` (INTEGER, 0/1). When 1, the kid
sees only the channels listed in `profile_channels` (plus channels the
kid owns); an empty list then means "nothing", not "everything".
When 0 the profile is unrestricted and `profile_channels` is ignored.

Backfill: any profile that already has `profile_channels` rows was
restricted under the old "non-empty list = restricted" rule.

Idempotent: probes PRAGMA table_info and only ADDs if missing.
"""

import sqlite3


def up(conn: sqlite3.Connection) -> None:
    tables = {row["name"] for row in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    if "profiles" not in tables:
        return
    cols = {row["name"] for row in
            conn.execute("PRAGMA table_info(profiles)").fetchall()}
    if "channels_restricted" not in cols:
        conn.execute(
            "ALTER TABLE profiles ADD COLUMN "
            "channels_restricted INTEGER NOT NULL DEFAULT 0"
        )
    if "profile_channels" in tables:
        conn.execute(
            "UPDATE profiles SET channels_restricted = 1 "
            "WHERE id IN (SELECT DISTINCT profile_id FROM profile_channels)"
        )
