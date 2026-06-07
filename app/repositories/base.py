from __future__ import annotations
"""Base repository with shared helpers."""

import sqlite3


class BaseRepository:
    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def _row_to_dict(self, row: sqlite3.Row | None) -> dict | None:
        return dict(row) if row else None

    def _rows_to_dicts(self, rows: list[sqlite3.Row]) -> list[dict]:
        return [dict(r) for r in rows]
