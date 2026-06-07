from __future__ import annotations
"""Profile channel video repository — per-kid channel bookmarks."""

from app.models.video import Video
from app.repositories.base import BaseRepository


class ProfileChannelVideoRepository(BaseRepository):

    def add(self, profile_id: int, channel_id: int,
            video_id: str) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO profile_channel_videos "
            "(profile_id, channel_id, video_id) VALUES (?, ?, ?)",
            (profile_id, channel_id, video_id),
        )
        self.db.commit()

    def remove(self, profile_id: int, channel_id: int,
               video_id: str) -> None:
        self.db.execute(
            "DELETE FROM profile_channel_videos "
            "WHERE profile_id = ? AND channel_id = ? AND video_id = ?",
            (profile_id, channel_id, video_id),
        )
        self.db.commit()

    def list_video_ids_for_channel(self, profile_id: int, channel_id: int,
                                   limit: int = 24,
                                   offset: int = 0) -> list[str]:
        rows = self.db.execute(
            "SELECT pcv.video_id FROM profile_channel_videos pcv "
            "JOIN videos v ON pcv.video_id = v.video_id "
            "WHERE pcv.profile_id = ? AND pcv.channel_id = ? "
            "AND v.status = 'active' AND v.download_status = 'ready' "
            "ORDER BY pcv.position, pcv.added_at DESC "
            "LIMIT ? OFFSET ?",
            (profile_id, channel_id, limit, offset),
        ).fetchall()
        return [r["video_id"] for r in rows]

    def count_for_channel(self, profile_id: int,
                          channel_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) as n FROM profile_channel_videos pcv "
            "JOIN videos v ON pcv.video_id = v.video_id "
            "WHERE pcv.profile_id = ? AND pcv.channel_id = ? "
            "AND v.status = 'active' AND v.download_status = 'ready'",
            (profile_id, channel_id),
        ).fetchone()
        return int(row["n"])

    def is_bookmarked(self, profile_id: int, channel_id: int,
                      video_id: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM profile_channel_videos "
            "WHERE profile_id = ? AND channel_id = ? AND video_id = ?",
            (profile_id, channel_id, video_id),
        ).fetchone()
        return row is not None

    def channels_for_video(self, profile_id: int,
                           video_id: str) -> list[int]:
        rows = self.db.execute(
            "SELECT channel_id FROM profile_channel_videos "
            "WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id),
        ).fetchall()
        return [r["channel_id"] for r in rows]
