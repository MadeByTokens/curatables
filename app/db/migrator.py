from __future__ import annotations
"""Forward-only schema migration runner.

Every schema change is a numbered file under `app/db/migrations/`:
  - NNNN_slug.sql  — straight SQL applied via executescript.
  - NNNN_slug.py   — has `def up(conn): ...` for logic that needs
                     Python (e.g. idempotent ALTER TABLE ADD COLUMN
                     on SQLite, which lacks IF NOT EXISTS).

The tracking table `schema_migrations(version, applied_at, name)` is
the single source of truth for what's been run. Running the migrator
against a partially-applied DB re-runs only the missing files, in
order.

Bootstrap: a DB that predates the migrator (has `videos` but not
`schema_migrations`) is stamped as already at version 1 and the
runner picks up from 0002. Fresh DBs run 0001 normally.
"""

import importlib.util
import logging
import re
import sqlite3
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

_FILE_RE = re.compile(r"^(\d{4})_([a-z0-9_]+)\.(sql|py)$")


def _ensure_tracking_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "  version INTEGER PRIMARY KEY, "
        "  applied_at TEXT NOT NULL DEFAULT (datetime('now')), "
        "  name TEXT NOT NULL"
        ")"
    )
    conn.commit()


def _db_predates_migrator(conn: sqlite3.Connection) -> bool:
    """True iff the DB has substantive tables (like `videos`) but no
    schema_migrations. That means it was created by the pre-migrator
    code path and should be stamped as already at v1."""
    row = conn.execute(
        "SELECT name FROM sqlite_master "
        "WHERE type='table' AND name IN ('schema_migrations', 'videos')"
    ).fetchall()
    names = {r["name"] for r in row}
    return "videos" in names and "schema_migrations" not in names


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    return {int(r["version"]) for r in
            conn.execute("SELECT version FROM schema_migrations").fetchall()}


def _load_py_migration(path: Path) -> Callable[[sqlite3.Connection], None]:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "up"):
        raise RuntimeError(
            f"Migration {path.name} has no 'up(conn)' function")
    return module.up


def _discover(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    out = []
    for p in sorted(migrations_dir.iterdir()):
        m = _FILE_RE.match(p.name)
        if m:
            version = int(m.group(1))
            name = m.group(2)
            out.append((version, name, p))
    out.sort(key=lambda t: t[0])
    return out


def apply_pending(conn: sqlite3.Connection,
                  migrations_dir: Path) -> int:
    """Apply every migration whose version isn't already in
    schema_migrations. Returns the number actually applied."""
    # Detect pre-migrator DBs BEFORE creating the tracking table —
    # otherwise _ensure_tracking_table makes schema_migrations exist
    # and the "looks like an old DB" heuristic no longer fires.
    predates = _db_predates_migrator(conn)

    _ensure_tracking_table(conn)

    if predates:
        conn.execute(
            "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
            (1, "initial_preexisting"),
        )
        conn.commit()
        logger.info(
            "Stamped pre-existing DB as at migration 0001_initial "
            "(no rerun — schema already present).")

    applied = _applied_versions(conn)
    migrations = _discover(migrations_dir)
    count = 0

    for version, name, path in migrations:
        if version in applied:
            continue
        logger.info("Applying migration %04d_%s", version, name)
        try:
            if path.suffix == ".sql":
                conn.executescript(path.read_text())
            else:
                up = _load_py_migration(path)
                up(conn)
            conn.execute(
                "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                (version, name),
            )
            conn.commit()
            count += 1
        except Exception:
            conn.rollback()
            logger.exception("Migration %04d_%s failed", version, name)
            raise

    return count


def migrations_dir() -> Path:
    return Path(__file__).parent / "migrations"
