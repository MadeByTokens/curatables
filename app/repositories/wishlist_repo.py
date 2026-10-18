"""Kid video requests (the wishlist): pending → approved | rejected.

The parent can also set one aside without deciding: `removed` (it was
pending) or `allowed_again` (it was rejected). The kid sees neither and
may ask again; Put back restores what it was."""

from __future__ import annotations

import builtins
from dataclasses import dataclass

from app.backends.base import VideoMetadata
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
    original_title: str = ""
    description: str = ""
    original_description: str = ""
    upload_date: str = ""
    view_count: int = 0
    target_channel_id: int | None = None
    suggested_channel: str = ""
    auto_added: int = 0
    profile_name: str = ""

    @property
    def has_details(self) -> bool:
        """YouTube's full details are stored (search results carry no
        upload date; a full lookup always does)."""
        return bool(self.upload_date)

    def metadata(self) -> VideoMetadata:
        """What YouTube said about the video, as the backend returned it."""
        return VideoMetadata(
            video_id=self.raw_id, title=self.original_title or self.title,
            channel=self.channel, duration=self.duration, view_count=self.view_count,
            upload_date=self.upload_date, description=self.original_description,
            thumbnail_url=self.thumbnail_url, extractor=self.extractor, original_url=self.url)


# Set aside by the parent: what Put back restores each one to.
SET_ASIDE = {"removed": "pending", "allowed_again": "rejected"}
_HIDDEN_FROM_KID = "w.status NOT IN ('removed', 'allowed_again')"


class WishlistRepository(BaseRepository):

    _SELECT = (
        "SELECT w.*, COALESCE(NULLIF(p.display_name, ''), p.name, '') AS profile_name "
        "FROM wishlist_requests w LEFT JOIN profiles p ON p.id = w.profile_id"
    )

    def add(self, profile_id: int, video_id: str, meta: VideoMetadata, url: str) -> int:
        """Record a pending request and return its id; asking again for the
        same video keeps the first request (one the parent set aside
        waits again)."""
        self.db.execute(
            "INSERT OR IGNORE INTO wishlist_requests (profile_id, video_id, extractor, "
            "raw_id, title, original_title, description, original_description, channel, "
            "duration, upload_date, view_count, url, thumbnail_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (profile_id, video_id, meta.extractor, meta.video_id, meta.title, meta.title,
             "", meta.description, meta.channel, meta.duration,
             meta.upload_date, meta.view_count, url, meta.thumbnail_url))
        self.db.execute(
            "UPDATE wishlist_requests SET status = 'pending', decided_at = NULL, auto_added = 0, "
            "created_at = datetime('now') WHERE profile_id = ? AND video_id = ? "
            "AND status IN ('removed', 'allowed_again')", (profile_id, video_id))
        self.db.commit()
        row = self.db.execute(
            "SELECT id FROM wishlist_requests WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id)).fetchone()
        return int(row["id"])

    def latest_for_video(self, video_id: str) -> WishlistRequest | None:
        """Any kid's request for this video: its stored details save asking
        YouTube again."""
        row = self.db.execute(
            f"{self._SELECT} WHERE w.video_id = ? ORDER BY w.id DESC LIMIT 1",
            (video_id,)).fetchone()
        return self._to_model(row) if row else None

    def choose(self, request_id: int, title: str, description: str,
               target_channel_id: int | None, suggested_channel: str = "") -> None:
        """The kid's own title, description and channel for a request: one
        of their channels, or the name of a new one for the parent to make."""
        self.db.execute(
            "UPDATE wishlist_requests SET title = ?, description = ?, target_channel_id = ?, "
            "suggested_channel = ? WHERE id = ?",
            (title, description, target_channel_id, suggested_channel, request_id))
        self.db.commit()

    def set_aside(self, request_id: int) -> None:
        """The parent drops a pending request, or takes back a rejection,
        without deleting it: the kid may ask again, and Put back undoes it."""
        self.db.execute(
            "UPDATE wishlist_requests SET status = CASE status WHEN 'pending' THEN 'removed' "
            "ELSE 'allowed_again' END, decided_at = datetime('now') "
            "WHERE id = ? AND status IN ('pending', 'rejected')", (request_id,))
        self.db.commit()

    def put_back(self, request_id: int) -> str | None:
        """Undo set_aside. Returns the status restored, if any."""
        item = self.get(request_id)
        if item is None or item.status not in SET_ASIDE:
            return None
        status = SET_ASIDE[item.status]
        self.db.execute(
            "UPDATE wishlist_requests SET status = ?, "
            "decided_at = CASE WHEN ? = 'pending' THEN NULL ELSE datetime('now') END "
            "WHERE id = ?", (status, status, request_id))
        self.db.commit()
        return status

    def remove(self, request_id: int) -> None:
        """Forget a request, as if it was never made (the kid taking back
        their own): the video can be asked for again."""
        self.db.execute("DELETE FROM wishlist_requests WHERE id = ?", (request_id,))
        self.db.commit()

    def get(self, request_id: int) -> WishlistRequest | None:
        row = self.db.execute(
            f"{self._SELECT} WHERE w.id = ?", (request_id,)).fetchone()
        return self._to_model(row) if row else None

    def statuses_for(self, profile_id: int) -> dict[str, str]:
        """video_id → status of everything this kid has asked for."""
        rows = self.db.execute(
            "SELECT video_id, status FROM wishlist_requests w WHERE profile_id = ? "
            f"AND {_HIDDEN_FROM_KID}", (profile_id,)).fetchall()
        return {r["video_id"]: r["status"] for r in rows}

    def list_for_profile(self, profile_id: int, limit: int = 50) -> builtins.list[WishlistRequest]:
        rows = self.db.execute(
            f"{self._SELECT} WHERE w.profile_id = ? AND {_HIDDEN_FROM_KID} "
            "ORDER BY w.created_at DESC, w.id DESC LIMIT ?",
            (profile_id, limit)).fetchall()
        return [self._to_model(r) for r in rows]

    def list_by_status(self, status: str, limit: int = 100) -> builtins.list[WishlistRequest]:
        rows = self.db.execute(
            f"{self._SELECT} WHERE w.status = ? ORDER BY w.created_at ASC, w.id ASC LIMIT ?",
            (status, limit)).fetchall()
        return [self._to_model(r) for r in rows]

    def list_decided(self, limit: int = 20) -> builtins.list[WishlistRequest]:
        """Decided and set-aside requests, the latest first."""
        rows = self.db.execute(
            f"{self._SELECT} WHERE w.status IN ('approved', 'rejected', 'unavailable', "
            "'removed', 'allowed_again') "
            "ORDER BY w.decided_at DESC, w.id DESC LIMIT ?",
            (limit,)).fetchall()
        return [self._to_model(r) for r in rows]

    def count_pending(self) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS n FROM wishlist_requests WHERE status = 'pending'").fetchone()
        return int(row["n"])

    def fill_details(self, request_id: int, meta: VideoMetadata) -> None:
        """YouTube's full details for a request recorded from search
        results. The kid's own title is kept if they changed it."""
        self.db.execute(
            "UPDATE wishlist_requests SET "
            "title = CASE WHEN title = original_title THEN ? ELSE title END, "
            "original_title = ?, original_description = ?, channel = ?, duration = ?, "
            "upload_date = ?, view_count = ? WHERE id = ?",
            (meta.title, meta.title, meta.description, meta.channel, meta.duration,
             meta.upload_date, meta.view_count, request_id))
        self.db.commit()

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

    def reopen_approved(self, video_id: str) -> int:
        """Approved requests for a video that left the library wait again."""
        cur = self.db.execute(
            "UPDATE wishlist_requests SET status = 'pending', decided_at = NULL, auto_added = 0 "
            "WHERE video_id = ? AND status = 'approved'", (video_id,))
        self.db.commit()
        return cur.rowcount

    def mark_auto_added(self, request_id: int) -> None:
        self.db.execute("UPDATE wishlist_requests SET auto_added = 1 WHERE id = ?", (request_id,))
        self.db.commit()

    @staticmethod
    def _to_model(row) -> WishlistRequest:
        d = dict(row)
        return WishlistRequest(**d)
