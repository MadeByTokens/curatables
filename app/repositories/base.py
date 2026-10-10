"""Base repository holding the shared database connection."""

from __future__ import annotations

import sqlite3


class BaseRepository:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    @staticmethod
    def _new_id(cur: sqlite3.Cursor) -> int:
        """The rowid of the row an INSERT just added."""
        if cur.lastrowid is None:
            raise RuntimeError("INSERT did not produce a row id")
        return cur.lastrowid
