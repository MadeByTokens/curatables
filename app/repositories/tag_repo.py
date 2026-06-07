from __future__ import annotations
"""Tag repository — per-kid video tagging system."""

from app.models.tag import Tag
from app.repositories.base import BaseRepository


class TagRepository(BaseRepository):

    def get_or_create(self, name: str) -> int:
        # Race-free under concurrent callers: INSERT OR IGNORE leaves the
        # row alone if it already exists (either from a previous call or
        # a concurrent one that just won the write). The subsequent SELECT
        # always sees it because SQLite serializes writes.
        name = name.strip()
        self.db.execute(
            "INSERT OR IGNORE INTO tags (name) VALUES (?)", (name,)
        )
        self.db.commit()
        row = self.db.execute(
            "SELECT id FROM tags WHERE name = ? COLLATE NOCASE",
            (name,),
        ).fetchone()
        return int(row["id"])

    def add_to_video(self, profile_id: int, video_id: str,
                     tag_id: int) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO profile_video_tags "
            "(profile_id, video_id, tag_id) VALUES (?, ?, ?)",
            (profile_id, video_id, tag_id),
        )
        self.db.commit()

    def remove_from_video(self, profile_id: int, video_id: str,
                          tag_id: int) -> None:
        self.db.execute(
            "DELETE FROM profile_video_tags "
            "WHERE profile_id = ? AND video_id = ? AND tag_id = ?",
            (profile_id, video_id, tag_id),
        )
        self.db.commit()

    def list_for_video(self, profile_id: int,
                       video_id: str) -> list[Tag]:
        rows = self.db.execute(
            "SELECT t.id, t.name FROM tags t "
            "JOIN profile_video_tags pvt ON pvt.tag_id = t.id "
            "WHERE pvt.profile_id = ? AND pvt.video_id = ? "
            "ORDER BY t.name",
            (profile_id, video_id),
        ).fetchall()
        return [Tag(id=r["id"], name=r["name"]) for r in rows]

    def tag_cloud(self, profile_id: int,
                  channel_ids: list[int] | None = None) -> list[dict]:
        if channel_ids is not None and not channel_ids:
            return []
        sql = ("SELECT t.name, COUNT(*) as cnt FROM profile_video_tags pvt "
               "JOIN tags t ON pvt.tag_id = t.id "
               "JOIN videos v ON pvt.video_id = v.video_id "
               "WHERE pvt.profile_id = ? "
               "AND v.status = 'active' AND v.download_status = 'ready'")
        params: list = [profile_id]
        if channel_ids is not None:
            placeholders = ",".join("?" for _ in channel_ids)
            sql += f" AND v.channel_id IN ({placeholders})"
            params.extend(channel_ids)
        sql += " GROUP BY t.name ORDER BY cnt DESC"
        rows = self.db.execute(sql, params).fetchall()
        return [{"name": r["name"], "count": r["cnt"]} for r in rows]

    def list_videos_by_tag(self, profile_id: int, tag_name: str,
                           channel_ids: list[int] | None = None,
                           limit: int = 24, offset: int = 0) -> list[str]:
        sql = ("SELECT pvt.video_id FROM profile_video_tags pvt "
               "JOIN tags t ON pvt.tag_id = t.id "
               "JOIN videos v ON pvt.video_id = v.video_id "
               "WHERE pvt.profile_id = ? AND t.name = ? COLLATE NOCASE "
               "AND v.status = 'active' AND v.download_status = 'ready'")
        params: list = [profile_id, tag_name]
        if channel_ids is not None:
            placeholders = ",".join("?" for _ in channel_ids)
            sql += f" AND v.channel_id IN ({placeholders})"
            params.extend(channel_ids)
        sql += " ORDER BY v.title LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.db.execute(sql, params).fetchall()
        return [r["video_id"] for r in rows]

    def count_videos_by_tag(self, profile_id: int, tag_name: str,
                            channel_ids: list[int] | None = None) -> int:
        sql = ("SELECT COUNT(*) as n FROM profile_video_tags pvt "
               "JOIN tags t ON pvt.tag_id = t.id "
               "JOIN videos v ON pvt.video_id = v.video_id "
               "WHERE pvt.profile_id = ? AND t.name = ? COLLATE NOCASE "
               "AND v.status = 'active' AND v.download_status = 'ready'")
        params: list = [profile_id, tag_name]
        if channel_ids is not None:
            placeholders = ",".join("?" for _ in channel_ids)
            sql += f" AND v.channel_id IN ({placeholders})"
            params.extend(channel_ids)
        return int(self.db.execute(sql, params).fetchone()["n"])

    def remove_all_for_video(self, profile_id: int, video_id: str) -> None:
        self.db.execute(
            "DELETE FROM profile_video_tags "
            "WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
        self.db.commit()
