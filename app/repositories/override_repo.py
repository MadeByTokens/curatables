from __future__ import annotations
"""Profile video override repository — per-kid title/thumbnail overrides."""

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
        # Race-free upsert: try INSERT optimistically; on UNIQUE conflict
        # (concurrent tab already inserted), fall through to the selective
        # UPDATE path. Same pattern ChannelRepository.create uses.
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
        # Row already exists — UPDATE only the fields the caller specified.
        # None means "don't touch this column" (clear_field handles the
        # explicit set-to-NULL case).
        parts, vals = [], []
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
        """Explicitly NULL out one override field without touching the
        others. Different from upsert(field=None), which means 'don't
        touch this field'. `field` must be one of 'title', 'description'."""
        if field not in ("title", "description"):
            raise ValueError(f"Cannot clear field '{field}'")
        if self.get(profile_id, video_id) is None:
            return
        self.db.execute(
            f"UPDATE profile_video_overrides SET {field} = NULL "
            "WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
        self.db.commit()

    def clear_title(self, profile_id: int, video_id: str) -> None:
        self.clear_field(profile_id, video_id, "title")

    def delete(self, profile_id: int, video_id: str) -> None:
        self.db.execute(
            "DELETE FROM profile_video_overrides "
            "WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
        self.db.commit()
