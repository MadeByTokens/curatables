"""Kid video requests (the wishlist): pending → approved | rejected."""
from __future__ import annotations

import builtins
from dataclasses import dataclass

from app.repositories.base import BaseRepository


@dataclass
class WishlistRequest:
    id: int
    profile_id: int
    video_id: str
    extractor: str
    raw_id: str
    title: str
    channel: str
    duration: int
    url: str
    thumbnail_url: str
    status: str
    created_at: str
    decided_at: str | None = None
    profile_name: str = ""


class WishlistRepository(BaseRepository):

    def add(self, profile_id: int, video_id: str, extractor: str, raw_id: str,
            title: str, channel: str, duration: int, url: str,
            thumbnail_url: str) -> None:
        """Record a pending request; asking again for the same video is a no-op."""
        self.db.execute(
            "INSERT OR IGNORE INTO wishlist_requests (profile_id, video_id, extractor, "
            "raw_id, title, channel, duration, url, thumbnail_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, video_id, extractor, raw_id, title, channel, duration, url,
             thumbnail_url))
        self.db.commit()

    def get(self, request_id: int) -> WishlistRequest | None:
        row = self.db.execute(
            f"{self._SELECT} WHERE w.id = ?", (request_id,)).fetchone()
        return self._to_model(row) if row else None

    def statuses_for(self, profile_id: int) -> dict[str, str]:
        """video_id → status of everything this kid has asked for."""
        rows = self.db.execute(
            "SELECT video_id, status FROM wishlist_requests WHERE profile_id = ?",
            (profile_id,)).fetchall()
        return {r["video_id"]: r["status"] for r in rows}

    def list_for_profile(self, profile_id: int, limit: int = 50) -> builtins.list[WishlistRequest]:
        rows = self.db.execute(
            f"{self._SELECT} WHERE w.profile_id = ? ORDER BY w.created_at DESC, w.id DESC LIMIT ?",
            (profile_id, limit)).fetchall()
        return [self._to_model(r) for r in rows]

    def list_by_status(self, status: str, limit: int = 100) -> builtins.list[WishlistRequest]:
        rows = self.db.execute(
            f"{self._SELECT} WHERE w.status = ? ORDER BY w.created_at ASC, w.id ASC LIMIT ?",
            (status, limit)).fetchall()
        return [self._to_model(r) for r in rows]

    def count_pending(self) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM wishlist_requests WHERE status = 'pending'").fetchone()
        return int(row["n"])

    def decide(self, request_id: int, status: str) -> None:
        self.db.execute(
            "UPDATE wishlist_requests SET status = ?, decided_at = datetime('now') "
            "WHERE id = ?", (status, request_id))
        self.db.commit()

    def approve_video(self, video_id: str) -> int:
        """Mark every pending request for this video approved (it was added
        to the library). Returns how many."""
        cur = self.db.execute(
            "UPDATE wishlist_requests SET status = 'approved', decided_at = datetime('now') "
            "WHERE video_id = ? AND status = 'pending'", (video_id,))
        self.db.commit()
        return cur.rowcount

    _SELECT = (
        "SELECT w.*, COALESCE(NULLIF(p.display_name, ''), p.name, '') AS profile_name "
        "FROM wishlist_requests w LEFT JOIN profiles p ON p.id = w.profile_id"
    )

    @staticmethod
    def _to_model(row) -> WishlistRequest:
        d = dict(row)
        return WishlistRequest(**d)
