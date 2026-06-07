"""SQLite connection management."""

import sqlite3
from pathlib import Path


def create_connection(db_path: Path) -> sqlite3.Connection:
    """Create a new SQLite connection with standard settings."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_db_path(data_dir: Path) -> Path:
    return data_dir / "db" / "curatables.db"
