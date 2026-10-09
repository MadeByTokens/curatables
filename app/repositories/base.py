"""Base repository with shared helpers."""
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

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row else None

    def _rows_to_dicts(self, rows: list[sqlite3.Row]) -> list[dict]:
        return [dict(r) for r in rows]
