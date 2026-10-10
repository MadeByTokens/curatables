"""Per-kid video overrides: custom title, description and thumbnail flag."""

from __future__ import annotations

import sqlite3

from app.repositories.base import BaseRepository


class ProfileVideoOverrideRepository(BaseRepository):

    def get(self, profile_id: int, video_id: str) -> dict | None:
        row = self.db.execute(
            "SELECT title, description, has_custom_thumb "
            "FROM profile_video_overrides "
            "WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        ).fetchone()
        return dict(row) if row else None

    def upsert(self, profile_id: int, video_id: str,
               title: str | None = None,
               description: str | None = None,
               has_custom_thumb: int | None = None) -> None:
        # INSERT first; on a primary-key conflict (the row exists, perhaps
        # just inserted by a concurrent request) fall through to UPDATE.
        try:
            self.db.execute(
                "INSERT INTO profile_video_overrides "
                "(profile_id, video_id, title, description, has_custom_thumb) "
                "VALUES (?, ?, ?, ?, ?)",
                (profile_id, video_id, title, description,
                 has_custom_thumb or 0),
            )
            self.db.commit()
            return
        except sqlite3.IntegrityError:
            pass
        # None means "leave unchanged"; clear_field() sets a column to NULL.
        parts: list[str] = []
        vals: list[object] = []
        if title is not None:
            parts.append("title = ?")
            vals.append(title)
        if description is not None:
            parts.append("description = ?")
            vals.append(description)
        if has_custom_thumb is not None:
            parts.append("has_custom_thumb = ?")
            vals.append(has_custom_thumb)
        if parts:
            vals.extend([profile_id, video_id])
            self.db.execute(
                f"UPDATE profile_video_overrides SET {', '.join(parts)} "
                "WHERE profile_id = ? AND video_id = ?", vals,
            )
            self.db.commit()

    def bulk_get(self, profile_id: int,
                 video_ids: list[str]) -> dict[str, dict]:
        if not video_ids:
            return {}
        placeholders = ",".join("?" for _ in video_ids)
        rows = self.db.execute(
            f"SELECT video_id, title, description, has_custom_thumb "
            f"FROM profile_video_overrides "
            f"WHERE profile_id = ? AND video_id IN ({placeholders})",
            [profile_id] + list(video_ids),
        ).fetchall()
        return {r["video_id"]: dict(r) for r in rows}

    def clear_field(self, profile_id: int, video_id: str,
                    field: str) -> None:
        """Set one override column, 'title' or 'description', to NULL."""
        if field not in ("title", "description"):
            raise ValueError(f"Cannot clear field '{field}'")
        self.db.execute(
            f"UPDATE profile_video_overrides SET {field} = NULL "
            "WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
        self.db.commit()
