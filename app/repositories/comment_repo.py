"""Comment repository: threaded comments on videos."""

from __future__ import annotations

from app.models.comment import Comment
from app.repositories.base import BaseRepository
from app.repositories.channel_repo import kid_sees_channel

_SELECT_WITH_AUTHOR = (
    "SELECT c.*, COALESCE(NULLIF(p.display_name, ''), p.name, 'Parent') AS author_name "
    "FROM comments c LEFT JOIN profiles p ON c.profile_id = p.id "
)

# A comment on a video is visible to a kid (bound as the single ?) when
# it is the parent's, the kid's own, or written by a profile that can see
# the video's channel.
_VISIBLE_TO_KID = f"""(
  c.is_parent_user = 1
  OR c.profile_id = ?
  OR c.profile_id IN (
    SELECT a.id FROM profiles a
    JOIN videos v ON v.video_id = c.video_id
    JOIN channels ch ON ch.id = v.channel_id
    WHERE {kid_sees_channel('a', 'ch')}
  )
)"""


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
        return self._new_id(cur)

    def delete(self, comment_id: int) -> None:
        self.db.execute(
            "DELETE FROM comments WHERE parent_comment_id = ?", (comment_id,)
        )
        self.db.execute("DELETE FROM comments WHERE id = ?", (comment_id,))
        self.db.commit()

    def get_by_id(self, comment_id: int) -> Comment | None:
        row = self.db.execute(
            _SELECT_WITH_AUTHOR + "WHERE c.id = ?",
            (comment_id,),
        ).fetchone()
        return self._to_model(row) if row else None

    # Pages are counted in top-level comments so a reply thread is never
    # split across two pages.

    def list_top_level_for_video(self, video_id: str,
                                 limit: int = 20,
                                 offset: int = 0) -> list[Comment]:
        rows = self.db.execute(
            _SELECT_WITH_AUTHOR + "WHERE c.video_id = ? AND c.parent_comment_id IS NULL "
            "ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?",
            (video_id, limit, offset),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_top_level_visible_to(self, video_id: str,
                                  viewer_profile_id: int,
                                  limit: int = 20,
                                  offset: int = 0) -> list[Comment]:
        rows = self.db.execute(
            _SELECT_WITH_AUTHOR + "WHERE c.video_id = ? AND c.parent_comment_id IS NULL "
            f"AND {_VISIBLE_TO_KID} "
            "ORDER BY c.created_at DESC, c.id DESC LIMIT ? OFFSET ?",
            (video_id, viewer_profile_id, limit, offset),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_replies_for(self, top_level_ids: list[int],
                         viewer_profile_id: int | None = None) -> list[Comment]:
        """Replies to the given comments; with viewer_profile_id, only the
        ones that kid may see (same rule as top-level comments)."""
        if not top_level_ids:
            return []
        placeholders = ",".join("?" for _ in top_level_ids)
        params: list = list(top_level_ids)
        visible = ""
        if viewer_profile_id is not None:
            visible = f"AND {_VISIBLE_TO_KID} "
            params.append(viewer_profile_id)
        rows = self.db.execute(
            _SELECT_WITH_AUTHOR + f"WHERE c.parent_comment_id IN ({placeholders}) "
            f"{visible}"
            f"ORDER BY c.created_at ASC",
            params,
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
            f"AND {_VISIBLE_TO_KID}",
            (video_id, viewer_profile_id),
        ).fetchone()
        return int(row["cnt"])

    def list_recent(self, limit: int = 50) -> list[Comment]:
        """Recent comments across all videos, for parent moderation."""
        rows = self.db.execute(
            "SELECT c.*, "
            "COALESCE(NULLIF(p.display_name, ''), p.name, 'Parent') as author_name, "
            "COALESCE(v.title, c.video_id) as video_title "
            "FROM comments c "
            "LEFT JOIN profiles p ON c.profile_id = p.id "
            "LEFT JOIN videos v ON v.video_id = c.video_id "
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
            _SELECT_WITH_AUTHOR + "WHERE c.profile_id = ? "
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
            video_title=d.get("video_title") or "",
        )
