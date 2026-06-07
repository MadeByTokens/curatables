from __future__ import annotations
"""Reaction repository — emoji reactions on videos."""

from app.repositories.base import BaseRepository


class ReactionRepository(BaseRepository):

    def get(self, profile_id: int, video_id: str) -> str | None:
        """Return the emoji for this profile+video, or None."""
        row = self.db.execute(
            "SELECT emoji FROM reactions WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        ).fetchone()
        return row["emoji"] if row else None

    def upsert(self, profile_id: int, video_id: str, emoji: str) -> None:
        """Set or replace a reaction."""
        self.db.execute(
            "INSERT INTO reactions (profile_id, video_id, emoji) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT(profile_id, video_id) DO UPDATE SET emoji = ?, created_at = datetime('now')",
            (profile_id, video_id, emoji, emoji),
        )
        self.db.commit()

    def delete(self, profile_id: int, video_id: str) -> None:
        self.db.execute(
            "DELETE FROM reactions WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
        self.db.commit()

    def counts_for_video(self, video_id: str) -> dict[str, int]:
        """Return {emoji: count} for a video."""
        rows = self.db.execute(
            "SELECT emoji, COUNT(*) as cnt FROM reactions "
            "WHERE video_id = ? GROUP BY emoji",
            (video_id,),
        ).fetchall()
        return {r["emoji"]: r["cnt"] for r in rows}

    def count_since(self, since: str | None = None) -> int:
        sql = "SELECT COUNT(*) as n FROM reactions"
        params: list = []
        if since:
            sql += " WHERE created_at >= ?"
            params.append(since)
        return int(self.db.execute(sql, params).fetchone()["n"])

    def count_by_profile(self, profile_id: int,
                         since: str | None = None) -> int:
        sql = "SELECT COUNT(*) as n FROM reactions WHERE profile_id = ?"
        params: list = [profile_id]
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        return int(self.db.execute(sql, params).fetchone()["n"])

    def list_for_video_with_profiles(self, video_id: str) -> list[dict]:
        rows = self.db.execute(
            "SELECT r.emoji, "
            "COALESCE(p.display_name, p.name, 'Unknown') as profile_name "
            "FROM reactions r "
            "LEFT JOIN profiles p ON r.profile_id = p.id "
            "WHERE r.video_id = ? "
            "ORDER BY r.created_at ASC",
            (video_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_by_profile(self, profile_id: int,
                        limit: int = 20) -> list[dict]:
        rows = self.db.execute(
            "SELECT r.emoji, r.video_id, r.created_at, "
            "v.title as video_title "
            "FROM reactions r "
            "LEFT JOIN videos v ON r.video_id = v.video_id "
            "WHERE r.profile_id = ? "
            "ORDER BY r.created_at DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def counts_for_videos(self, video_ids: list[str]) -> dict[str, dict[str, int]]:
        """Bulk fetch {video_id: {emoji: count}} for multiple videos."""
        if not video_ids:
            return {}
        placeholders = ",".join("?" for _ in video_ids)
        rows = self.db.execute(
            f"SELECT video_id, emoji, COUNT(*) as cnt FROM reactions "
            f"WHERE video_id IN ({placeholders}) GROUP BY video_id, emoji",
            video_ids,
        ).fetchall()
        result: dict[str, dict[str, int]] = {}
        for r in rows:
            vid = r["video_id"]
            if vid not in result:
                result[vid] = {}
            result[vid][r["emoji"]] = r["cnt"]
        return result
