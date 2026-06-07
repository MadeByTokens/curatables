"""Tests for the forward-only schema migrator."""

import sqlite3
import threading
from pathlib import Path

import pytest

from app.db.migrator import apply_pending, migrations_dir


def _conn() -> sqlite3.Connection:
    # File-backed (tempfile) so we can open multiple connections from
    # threads. :memory: won't do for the concurrency test.
    import tempfile
    path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
    c = sqlite3.connect(path, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA foreign_keys=ON")
    return c


def _has_column(conn, table, col) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(r["name"] == col for r in rows)


class TestMigratorFreshDB:
    def test_applies_all_migrations(self):
        conn = _conn()
        n = apply_pending(conn, migrations_dir())
        # 0001_initial + 0002_bootstrap_existing at minimum
        assert n >= 2

        # Expected tables exist
        names = {r["name"] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()}
        for t in ("videos", "channels", "profiles", "profile_video_overrides",
                  "tags", "profile_video_tags", "profile_channel_videos",
                  "schema_migrations"):
            assert t in names, f"missing table: {t}"

        # New columns from 0001 are present
        assert _has_column(conn, "channels", "banner_filename")
        assert _has_column(conn, "profile_video_overrides", "description")

        # schema_migrations records both migrations applied
        versions = {r["version"] for r in conn.execute(
            "SELECT version FROM schema_migrations").fetchall()}
        assert 1 in versions
        assert 2 in versions
        conn.close()

    def test_second_run_is_noop(self):
        conn = _conn()
        apply_pending(conn, migrations_dir())
        n2 = apply_pending(conn, migrations_dir())
        assert n2 == 0
        conn.close()


class TestMigratorBootstrap:
    def test_pre_migrator_db_is_stamped_at_v1(self, tmp_path):
        """A DB that has the old schema applied but no schema_migrations
        table should get stamped as at version 1 (don't re-run 0001)
        and then apply 0002+ as needed."""
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Apply the OLD schema (0001 sans the newer columns) to simulate
        # a pre-migrator deployment. Easiest: apply 0001 fully, then
        # drop the columns added later (which SQLite can't do), so
        # instead we hand-build a minimal video/channels schema.
        conn.executescript("""
            CREATE TABLE profiles (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                pin TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                avatar TEXT DEFAULT 'default',
                theme TEXT DEFAULT 'base',
                search_mode TEXT DEFAULT 'disabled',
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE channels (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT DEFAULT '',
                position INTEGER DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                owner_profile_id INTEGER REFERENCES profiles(id) ON DELETE SET NULL
                -- NOTE: no banner_filename / icon_filename / color
            );
            CREATE TABLE videos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                original_title TEXT NOT NULL
            );
            CREATE TABLE profile_video_overrides (
                profile_id INTEGER NOT NULL,
                video_id TEXT NOT NULL,
                title TEXT,
                has_custom_thumb INTEGER DEFAULT 0,
                -- NOTE: no description column
                PRIMARY KEY (profile_id, video_id)
            );
        """)
        conn.commit()

        # Run migrator — should stamp v1 without rerunning, then apply
        # 0002_bootstrap to add the missing columns.
        n = apply_pending(conn, migrations_dir())
        # 0002 applied; 0001 was stamped without running.
        assert n >= 1

        # The missing columns now exist.
        assert _has_column(conn, "channels", "banner_filename")
        assert _has_column(conn, "channels", "color")
        assert _has_column(conn, "profile_video_overrides", "description")

        # v1 stamped
        versions = {r["version"] for r in conn.execute(
            "SELECT version FROM schema_migrations").fetchall()}
        assert 1 in versions
        conn.close()


class TestMigratorMultiVersionLeap:
    """Pins the contract that a self-hoster on v0.3 can upgrade
    straight to a later version (e.g. v0.5) without booting any
    intermediate releases. The migrator simply walks every pending
    file in order — no per-version manual steps required."""

    def test_db_stamped_at_v1_leaps_to_latest(self, tmp_path):
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        # Build the v1 schema by running 0001 against a fresh DB,
        # then mark schema_migrations at version 1 so the runner
        # picks up from 0002.
        from app.db.migrator import _discover
        migs = _discover(migrations_dir())
        first = next(p for v, _, p in migs if v == 1)
        conn.executescript(first.read_text())
        conn.execute(
            "CREATE TABLE schema_migrations ("
            "version INTEGER PRIMARY KEY, "
            "applied_at TEXT NOT NULL DEFAULT (datetime('now')), "
            "name TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (1, 'initial')"
        )
        conn.commit()

        # One call applies every migration after v1 in one go.
        applied = apply_pending(conn, migrations_dir())
        latest = max(v for v, _, _ in migs)
        assert applied == latest - 1, (
            f"expected to apply {latest - 1} migrations, applied {applied}"
        )
        # And the tracking table now records every version.
        recorded = {r["version"] for r in conn.execute(
            "SELECT version FROM schema_migrations").fetchall()}
        for v, _, _ in migs:
            assert v in recorded
        conn.close()


class TestMigratorConcurrency:
    def test_two_processes_dont_double_apply(self):
        """Two racing startups should not both think they applied
        migration 0001. INSERT into schema_migrations has a PRIMARY KEY
        on version, so the second INSERT fails and the migrator
        rolls back. Simulate via threads sharing a file-backed DB."""
        import tempfile
        path = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
        # Initialize once
        sqlite3.connect(path).close()

        errors = []
        counts = []
        barrier = threading.Barrier(2)

        def worker():
            barrier.wait()
            try:
                c = sqlite3.connect(path, check_same_thread=False)
                c.row_factory = sqlite3.Row
                c.execute("PRAGMA journal_mode=WAL")
                n = apply_pending(c, migrations_dir())
                counts.append(n)
                c.close()
            except Exception as e:
                errors.append(e)

        t1 = threading.Thread(target=worker)
        t2 = threading.Thread(target=worker)
        t1.start(); t2.start()
        t1.join(); t2.join()

        # At most one worker can succeed without IntegrityError; the
        # other should either retry silently or surface the error
        # (acceptable — startup loudly crashes rather than double-applies).
        # Either way, the final DB has exactly one row per version.
        c = sqlite3.connect(path)
        c.row_factory = sqlite3.Row
        rows = c.execute(
            "SELECT version, COUNT(*) AS n FROM schema_migrations "
            "GROUP BY version").fetchall()
        for r in rows:
            assert r["n"] == 1, f"version {r['version']} applied twice"
        c.close()
