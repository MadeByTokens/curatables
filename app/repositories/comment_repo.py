from __future__ import annotations
"""Comment repository — threaded comments on videos."""

from app.models.comment import Comment
from app.repositories.base import BaseRepository


class CommentRepository(BaseRepository):

    def create(self, video_id: str, body: str, profile_id: int | None,
               is_parent_user: int = 0,
               parent_comment_id: int | None = None) -> int:
        cur = self.db.execute(
            "INSERT INTO comments (video_id, body, profile_id, is_parent_user, parent_comment_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (video_id, body, profile_id, is_parent_user, parent_comment_id),
        )
        self.db.commit()
        return cur.lastrowid

    def delete(self, comment_id: int) -> None:
        # Delete replies first, then the comment
        self.db.execute(
            "DELETE FROM comments WHERE parent_comment_id = ?", (comment_id,)
        )
        self.db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        self.db.commit()

    def get_by_id(self, comment_id: int) -> Comment | None:
        row = self.db.execute(
            "SELECT c.*, "
            "COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            "COALESCE(p.avatar, 'default') as author_avatar "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "WHERE c.id = ?",
            (comment_id,),
        ).fetchone()
        return self._to_model(row) if row else None

    def list_for_video(self, video_id: str) -> list[Comment]:
        """Get all comments for a video with author info. Returns flat list."""
        rows = self.db.execute(
            "SELECT c.*, "
            "COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            "COALESCE(p.avatar, 'default') as author_avatar "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "WHERE c.video_id = ? "
            "ORDER BY c.created_at ASC",
            (video_id,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_for_video_visible_to(self, video_id: str,
                                  viewer_profile_id: int) -> list[Comment]:
        """Get comments visible to a specific child profile.
        A child sees: their own comments, parent comments, and comments
        from profiles that share at least one channel with them."""
        rows = self.db.execute(
            "SELECT c.*, "
            "COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            "COALESCE(p.avatar, 'default') as author_avatar "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "WHERE c.video_id = ? "
            "AND ("
            "  c.is_parent_user = 1 "
            "  OR c.profile_id = ? "
            "  OR c.profile_id IN ("
            "    SELECT DISTINCT pc2.profile_id "
            "    FROM profile_channels pc1 "
            "    JOIN profile_channels pc2 ON pc1.channel_id = pc2.channel_id "
            "    WHERE pc1.profile_id = ? AND pc2.profile_id != pc1.profile_id"
            "  )"
            ") "
            "ORDER BY c.created_at ASC",
            (video_id, viewer_profile_id, viewer_profile_id),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_for_video(self, video_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) as cnt FROM comments WHERE video_id = ?",
            (video_id,),
        ).fetchone()
        return row["cnt"]

    # --- Paginated reads: page by top-level comment so reply threads
    # stay intact (no half-thread cut off at the page boundary). ---

    def list_top_level_for_video(self, video_id: str,
                                 limit: int = 20,
                                 offset: int = 0) -> list[Comment]:
        rows = self.db.execute(
            "SELECT c.*, "
            "COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            "COALESCE(p.avatar, 'default') as author_avatar "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "WHERE c.video_id = ? AND c.parent_comment_id IS NULL "
            "ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?",
            (video_id, limit, offset),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_top_level_visible_to(self, video_id: str,
                                  viewer_profile_id: int,
                                  limit: int = 20,
                                  offset: int = 0) -> list[Comment]:
        rows = self.db.execute(
            "SELECT c.*, "
            "COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            "COALESCE(p.avatar, 'default') as author_avatar "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "WHERE c.video_id = ? AND c.parent_comment_id IS NULL "
            "AND ("
            "  c.is_parent_user = 1 "
            "  OR c.profile_id = ? "
            "  OR c.profile_id IN ("
            "    SELECT DISTINCT pc2.profile_id "
            "    FROM profile_channels pc1 "
            "    JOIN profile_channels pc2 ON pc1.channel_id = pc2.channel_id "
            "    WHERE pc1.profile_id = ? AND pc2.profile_id != pc1.profile_id"
            "  )"
            ") "
            "ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?",
            (video_id, viewer_profile_id, viewer_profile_id, limit, offset),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_replies_for(self, top_level_ids: list[int]) -> list[Comment]:
        if not top_level_ids:
            return []
        placeholders = ",".join("?" for _ in top_level_ids)
        rows = self.db.execute(
            f"SELECT c.*, "
            f"COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            f"COALESCE(p.avatar, 'default') as author_avatar "
            f"FROM comments c "
            f"LEFT JOIN profiles p ON c.profile_id = p.id "
            f"WHERE c.parent_comment_id IN ({placeholders}) "
            f"ORDER BY c.created_at ASC",
            list(top_level_ids),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_top_level_for_video(self, video_id: str) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) as cnt FROM comments "
            "WHERE video_id = ? AND parent_comment_id IS NULL",
            (video_id,),
        ).fetchone()
        return int(row["cnt"])

    def count_top_level_visible_to(self, video_id: str,
                                   viewer_profile_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) as cnt FROM comments c "
            "WHERE c.video_id = ? AND c.parent_comment_id IS NULL "
            "AND ("
            "  c.is_parent_user = 1 "
            "  OR c.profile_id = ? "
            "  OR c.profile_id IN ("
            "    SELECT DISTINCT pc2.profile_id "
            "    FROM profile_channels pc1 "
            "    JOIN profile_channels pc2 ON pc1.channel_id = pc2.channel_id "
            "    WHERE pc1.profile_id = ? AND pc2.profile_id != pc1.profile_id"
            "  )"
            ")",
            (video_id, viewer_profile_id, viewer_profile_id),
        ).fetchone()
        return int(row["cnt"])

    def list_recent(self, limit: int = 50) -> list[Comment]:
        """Get recent comments across all videos (for parent moderation)."""
        rows = self.db.execute(
            "SELECT c.*, "
            "COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            "COALESCE(p.avatar, 'default') as author_avatar "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "ORDER BY c.created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_since(self, since: str | None = None) -> int:
        sql = "SELECT COUNT(*) as n FROM comments"
        params: list = []
        if since:
            sql += " WHERE created_at >= ?"
            params.append(since)
        return int(self.db.execute(sql, params).fetchone()["n"])

    def count_by_profile(self, profile_id: int,
                         since: str | None = None) -> int:
        sql = "SELECT COUNT(*) as n FROM comments WHERE profile_id = ?"
        params: list = [profile_id]
        if since:
            sql += " AND created_at >= ?"
            params.append(since)
        return int(self.db.execute(sql, params).fetchone()["n"])

    def list_recent_by_profile(self, profile_id: int,
                               limit: int = 20) -> list[Comment]:
        rows = self.db.execute(
            "SELECT c.*, "
            "COALESCE(p.display_name, p.name, 'Parent') as author_name, "
            "COALESCE(p.avatar, 'default') as author_avatar "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "WHERE c.profile_id = ? "
            "ORDER BY c.created_at DESC LIMIT ?",
            (profile_id, limit),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def _to_model(self, row) -> Comment:
        d = dict(row)
        return Comment(
            id=d["id"],
            video_id=d["video_id"],
            body=d["body"],
            profile_id=d.get("profile_id"),
            parent_comment_id=d.get("parent_comment_id"),
            is_parent_user=d.get("is_parent_user", 0),
            created_at=d.get("created_at", ""),
            author_name=d.get("author_name", ""),
            author_avatar=d.get("author_avatar", "default"),
        )
