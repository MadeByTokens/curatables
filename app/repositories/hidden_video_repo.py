"""Videos a kid hid from their own lists; the parent is told about each."""

from __future__ import annotations

from app.repositories.base import BaseRepository

# Leaves out the videos a kid hid. `{col}` is the video_id column of the
# outer query; the kid's profile id is the one parameter.
NOT_HIDDEN_SQL = ("{col} NOT IN (SELECT video_id FROM profile_hidden_videos "
                  "WHERE profile_id = ?)")


class HiddenVideoRepository(BaseRepository):

    def hide(self, profile_id: int, video_id: str) -> None:
        # Hiding again after the parent let the kid see it again asks again.
        self.db.execute(
            "INSERT INTO profile_hidden_videos (profile_id, video_id) VALUES (?, ?) "
            "ON CONFLICT (profile_id, video_id) DO UPDATE "
            "SET hidden_at = datetime('now'), reviewed = 0",
            (profile_id, video_id),
        )
        self.db.commit()

    def unhide(self, profile_id: int, video_id: str) -> None:
        self.db.execute(
            "DELETE FROM profile_hidden_videos WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
        self.db.commit()

    def mark_reviewed(self, profile_id: int, video_id: str) -> None:
        self.db.execute(
            "UPDATE profile_hidden_videos SET reviewed = 1 "
            "WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        )
        self.db.commit()

    def _list(self, where: str, params: tuple, limit: int) -> list[dict]:
        rows = self.db.execute(
            "SELECT h.profile_id, h.video_id, h.hidden_at, h.reviewed, v.title, "
            "COALESCE(NULLIF(p.display_name, ''), p.name) AS kid_name "
            "FROM profile_hidden_videos h "
            "JOIN videos v ON v.video_id = h.video_id "
            "JOIN profiles p ON p.id = h.profile_id "
            f"WHERE {where} ORDER BY h.hidden_at DESC LIMIT ?",
            (*params, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def list_unreviewed(self, limit: int = 20) -> list[dict]:
        """What kids hid that the parent hasn't decided on yet, newest first."""
        return self._list("h.reviewed = 0", (), limit)

    def count_unreviewed(self) -> int:
        return int(self.db.execute(
            "SELECT COUNT(*) AS n FROM profile_hidden_videos WHERE reviewed = 0"
        ).fetchone()["n"])

    def for_video(self, video_id: str) -> list[dict]:
        return self._list("h.video_id = ?", (video_id,), 100)

    def for_profile(self, profile_id: int, limit: int = 200) -> list[dict]:
        return self._list("h.profile_id = ?", (profile_id,), limit)
