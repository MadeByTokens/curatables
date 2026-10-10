"""Forward-only schema migration runner.

Every schema change is a numbered file under `app/db/migrations/`:
  - NNNN_slug.sql: plain SQL applied via executescript.
  - NNNN_slug.py: defines `up(conn)` for changes that need Python
    (e.g. an idempotent ADD COLUMN, which SQLite cannot express with
    IF NOT EXISTS).

The `schema_migrations` table records which versions have run; each
startup applies only the missing files, in order.
"""

from __future__ import annotations

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


def _applied_versions(conn: sqlite3.Connection) -> set[int]:
    return {int(r["version"]) for r in
            conn.execute("SELECT version FROM schema_migrations").fetchall()}


def _load_py_migration(path: Path) -> Callable[[sqlite3.Connection], None]:
    spec = importlib.util.spec_from_file_location(path.stem, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load migration {path.name}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "up"):
        raise RuntimeError(
            f"Migration {path.name} has no 'up(conn)' function")
    up: Callable[[sqlite3.Connection], None] = module.up
    return up


def _discover(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    out = []
    for p in migrations_dir.iterdir():
        m = _FILE_RE.match(p.name)
        if m:
            out.append((int(m.group(1)), m.group(2), p))
    out.sort(key=lambda t: t[0])
    return out


def apply_pending(conn: sqlite3.Connection,
                  migrations_dir: Path) -> int:
    """Apply every migration whose version isn't already in
    schema_migrations. Returns the number actually applied."""
    _ensure_tracking_table(conn)
    applied = _applied_versions(conn)
    count = 0

    for version, name, path in _discover(migrations_dir):
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
