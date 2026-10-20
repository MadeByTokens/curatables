"""Video repository."""

from __future__ import annotations

import builtins
import re
import sqlite3
import unicodedata

from app.models import Video
from app.repositories.base import BaseRepository
from app.repositories.hidden_video_repo import NOT_HIDDEN_SQL


def fold_text(text: str) -> str:
    """Lower-case text with accents removed, for searching."""
    if text.isascii():
        # Most text, and 100x faster than decomposing it.
        return text.lower()
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c)).casefold()


class VideoRepository(BaseRepository):

    def get(self, video_id: str) -> Video | None:
        row = self.db.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if not row:
            return None
        return self._to_model(row)

    # Orders for the parent's content list. Least watched first helps the
    # parent pick videos to remove when the drive fills up.
    SORTS = {
        "newest": "added_at DESC, id DESC",
        "least_played": "play_count ASC, COALESCE(last_played_at, added_at) ASC, id ASC",
        "unwatched_longest": "COALESCE(last_played_at, added_at) ASC, id ASC",
        "largest": "COALESCE(file_size, 0) DESC, id DESC",
    }

    def list(self, status: str | None = "active",
             channel_id: int | None = None,
             limit: int = 30, offset: int = 0, sort: str = "newest") -> list[Video]:
        clauses = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)

        where = " AND ".join(clauses)
        if where:
            where = "WHERE " + where

        params.extend([limit, offset])
        rows = self.db.execute(
            f"SELECT * FROM videos {where} ORDER BY {self.SORTS.get(sort, self.SORTS['newest'])} "
            "LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count(self, status: str | None = "active",
              channel_id: int | None = None) -> int:
        clauses = []
        params: list = []
        if status is not None:
            clauses.append("status = ?")
            params.append(status)
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)

        where = " AND ".join(clauses)
        if where:
            where = "WHERE " + where

        row = self.db.execute(
            f"SELECT COUNT(*) as cnt FROM videos {where}", params
        ).fetchone()
        return int(row["cnt"])

    def list_ready(self, channel_id: int | None = None,
                   limit: int = 30, offset: int = 0,
                   hidden_for: int | None = None) -> builtins.list[Video]:
        """Videos kids can open: active, and downloaded or freed (a freed
        video downloads again when a kid opens it). `hidden_for` leaves out
        the videos that kid hid."""
        clauses = ["status = 'active'", "download_status IN ('ready', 'evicted')"]
        params: list = []
        if hidden_for is not None:
            clauses.append(NOT_HIDDEN_SQL.format(col="video_id"))
            params.append(hidden_for)
        order = "added_at DESC"
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)
            # A show's episodes play in order; everything else newest first.
            order = "episode_key IS NULL, episode_key, added_at DESC"
        where = "WHERE " + " AND ".join(clauses)
        params.extend([limit, offset])
        rows = self.db.execute(
            f"SELECT * FROM videos {where} ORDER BY {order} LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_ready(self, channel_id: int | None = None,
                    hidden_for: int | None = None) -> int:
        clauses = ["status = 'active'", "download_status IN ('ready', 'evicted')"]
        params: list = []
        if hidden_for is not None:
            clauses.append(NOT_HIDDEN_SQL.format(col="video_id"))
            params.append(hidden_for)
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        where = "WHERE " + " AND ".join(clauses)
        row = self.db.execute(
            f"SELECT COUNT(*) as cnt FROM videos {where}", params
        ).fetchone()
        return int(row["cnt"])

    def list_ready_by_channels(self, channel_ids: builtins.list[int],
                               limit: int = 30, offset: int = 0,
                               hidden_for: int | None = None) -> builtins.list[Video]:
        if not channel_ids:
            return []
        placeholders = ",".join("?" for _ in channel_ids)
        params: list = list(channel_ids)
        hidden = ""
        if hidden_for is not None:
            hidden = "AND " + NOT_HIDDEN_SQL.format(col="video_id") + " "
            params.append(hidden_for)
        params.extend([limit, offset])
        rows = self.db.execute(
            f"SELECT * FROM videos WHERE status = 'active' AND download_status IN ('ready', 'evicted') "
            f"AND channel_id IN ({placeholders}) {hidden}"
            f"ORDER BY added_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def next_episode(self, channel_id: int, episode_key: int,
                     hidden_for: int | None = None) -> Video | None:
        """The ready episode after this one in the same channel, skipping
        the ones `hidden_for` hid."""
        hidden, params = "", [channel_id, episode_key]
        if hidden_for is not None:
            hidden = "AND " + NOT_HIDDEN_SQL.format(col="video_id") + " "
            params.append(hidden_for)
        row = self.db.execute(
            "SELECT * FROM videos WHERE channel_id = ? AND status = 'active' "
            f"AND download_status = 'ready' AND episode_key > ? {hidden}"
            "ORDER BY episode_key LIMIT 1", params).fetchone()
        return self._to_model(row) if row else None

    def count_ready_by_channels(self, channel_ids: builtins.list[int],
                                hidden_for: int | None = None) -> int:
        if not channel_ids:
            return 0
        placeholders = ",".join("?" for _ in channel_ids)
        params: list = list(channel_ids)
        hidden = ""
        if hidden_for is not None:
            hidden = " AND " + NOT_HIDDEN_SQL.format(col="video_id")
            params.append(hidden_for)
        row = self.db.execute(
            f"SELECT COUNT(*) as cnt FROM videos "
            f"WHERE status = 'active' AND download_status IN ('ready', 'evicted') "
            f"AND channel_id IN ({placeholders}){hidden}",
            params,
        ).fetchone()
        return int(row["cnt"])

    def channel_ids_with_ready(self, hidden_for: int | None = None) -> set[int]:
        """Channels that hold at least one video kids can watch (that
        `hidden_for` didn't hide)."""
        hidden, params = "", []
        if hidden_for is not None:
            hidden = " AND " + NOT_HIDDEN_SQL.format(col="video_id")
            params.append(hidden_for)
        rows = self.db.execute(
            "SELECT DISTINCT channel_id FROM videos WHERE status = 'active' "
            "AND download_status IN ('ready', 'evicted') AND channel_id IS NOT NULL"
            + hidden, params).fetchall()
        return {r["channel_id"] for r in rows}

    def search_ready(self, query: str, channel_ids: builtins.list[int] | None = None,
                     limit: int = 24, offset: int = 0,
                     profile_id: int | None = None) -> builtins.list[Video]:
        """Search ready videos by title and description, matching from the
        start of a word: "park" finds "Park day" and "parks", not "Sparky".
        Accents and case are ignored ("vulcao" finds "Vulcão"). With a
        `profile_id`, that kid's own title, description and tags are
        searched as well, and the videos that kid hid are left out.
        `channel_ids` None searches every video; an empty list matches
        nothing."""
        sql = ("SELECT v.*, o.title AS kid_title, o.description AS kid_description, "
               "(SELECT group_concat(t.name, char(31)) FROM profile_video_tags pvt "
               " JOIN tags t ON t.id = pvt.tag_id "
               " WHERE pvt.profile_id = ? AND pvt.video_id = v.video_id) AS kid_tags "
               "FROM videos v LEFT JOIN profile_video_overrides o "
               "ON o.video_id = v.video_id AND o.profile_id = ? "
               "WHERE v.status = 'active' AND v.download_status IN ('ready', 'evicted') ")
        params: list = [profile_id, profile_id]
        if profile_id is not None:
            sql += "AND " + NOT_HIDDEN_SQL.format(col="v.video_id") + " "
            params.append(profile_id)
        if channel_ids is not None:
            if not channel_ids:
                return []
            sql += f"AND v.channel_id IN ({','.join('?' for _ in channel_ids)}) "
            params.extend(channel_ids)
        # Folding is done here, not in SQL: SQLite's LIKE folds neither
        # accents nor non-ASCII case.
        word_start = re.compile(r"(?<!\w)" + re.escape(fold_text(query)))
        # Stop reading once this page is full: a short query matches
        # most of a large library.
        rows: builtins.list = []
        for r in self.db.execute(sql + "ORDER BY v.added_at DESC", params):
            texts = (r["title"], r["description"], r["kid_title"],
                     r["kid_description"], r["kid_tags"])
            if any(t and word_start.search(fold_text(t)) for t in texts):
                rows.append(r)
                if len(rows) >= offset + limit:
                    break
        return [self._to_model(r) for r in rows[offset:offset + limit]]

    def insert(self, video: Video) -> None:
        self.db.execute(
            "INSERT OR IGNORE INTO videos "
            "(video_id, extractor, original_url, source_id, channel_id, "
            "title, original_title, channel_name, "
            "description, duration, upload_date, view_count, thumbnail_url, "
            "thumbnail_type, status, download_status, storage_mode, resolution) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                video.video_id, video.extractor, video.original_url,
                video.source_id, video.channel_id,
                video.title, video.original_title, video.channel_name,
                video.description, video.duration, video.upload_date,
                video.view_count, video.thumbnail_url, video.thumbnail_type,
                video.status, video.download_status, video.storage_mode,
                video.resolution,
            ),
        )
        self.db.commit()

    _UPDATABLE_FIELDS = {
        "title", "description", "channel_id", "channel_name", "status",
        "download_status", "download_error", "storage_mode", "resolution",
        "thumbnail_url", "thumbnail_type", "cached_at", "cache_expires_at",
        "file_size", "extractor", "original_url", "keep_forever",
        "origin", "origin_ref", "source_path", "episode_key",
    }

    def update(self, video_id: str, **fields) -> None:
        if not fields:
            return
        bad = set(fields) - self._UPDATABLE_FIELDS
        if bad:
            raise ValueError(f"Invalid fields for video update: {bad}")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())
        values.append(video_id)
        self.db.execute(
            f"UPDATE videos SET {set_clause} WHERE video_id = ?", values
        )
        self.db.commit()

    def delete(self, video_id: str) -> None:
        """Delete the video with its comments and reactions, which have no
        foreign key; if the same video is added again it starts clean."""
        self.db.execute("DELETE FROM comments WHERE video_id = ?", (video_id,))
        self.db.execute("DELETE FROM reactions WHERE video_id = ?", (video_id,))
        self.db.execute("DELETE FROM videos WHERE video_id = ?", (video_id,))
        self.db.commit()

    def sum_file_size(self) -> int:
        """Total bytes used by active videos."""
        row = self.db.execute(
            "SELECT COALESCE(SUM(file_size), 0) AS total FROM videos WHERE status = 'active'"
        ).fetchone()
        return int(row["total"])

    def count_downloading(self) -> int:
        """Number of videos currently in the downloading state."""
        row = self.db.execute(
            "SELECT COUNT(*) AS cnt FROM videos WHERE download_status = 'downloading'"
        ).fetchone()
        return int(row["cnt"])

    def list_pending(self) -> builtins.list[Video]:
        """Pending cached videos, oldest first (resumed at startup)."""
        rows = self.db.execute(
            "SELECT * FROM videos WHERE download_status = 'pending' "
            "AND storage_mode = 'cache' "
            "ORDER BY added_at ASC"
        ).fetchall()
        return [self._to_model(r) for r in rows]

    LIST_FILTERS = {
        "review": "status = 'review'",
        "hidden": "status = 'hidden'",
        "pending": "download_status IN ('pending', 'downloading', 'paused', 'stopped')",
        "error": "download_status = 'error'",
        "kid_hidden": "video_id IN (SELECT video_id FROM profile_hidden_videos)",
    }

    def list_filtered(self, filter_name: str, limit: int = 30,
                      offset: int = 0, sort: str = "newest") -> builtins.list[Video]:
        where = self.LIST_FILTERS[filter_name]
        rows = self.db.execute(
            f"SELECT * FROM videos WHERE {where} "
            f"ORDER BY {self.SORTS.get(sort, self.SORTS['newest'])} LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_filtered(self, filter_name: str) -> int:
        where = self.LIST_FILTERS[filter_name]
        row = self.db.execute(
            f"SELECT COUNT(*) AS cnt FROM videos WHERE {where}").fetchone()
        return int(row["cnt"])

    def ids_by_download_status(self, *statuses: str) -> builtins.list[str]:
        marks = ", ".join("?" for _ in statuses)
        rows = self.db.execute(
            f"SELECT video_id FROM videos WHERE download_status IN ({marks}) "
            "ORDER BY added_at, video_id", statuses).fetchall()
        return [r["video_id"] for r in rows]

    def download_statuses(self, video_ids: builtins.list[str]) -> dict[str, str]:
        if not video_ids:
            return {}
        placeholders = ",".join("?" for _ in video_ids)
        rows = self.db.execute(
            f"SELECT video_id, download_status FROM videos "
            f"WHERE video_id IN ({placeholders})",
            list(video_ids),
        ).fetchall()
        return {r["video_id"]: r["download_status"] for r in rows}

    def list_awaiting_review(self, limit: int = 20) -> builtins.list[Video]:
        rows = self.db.execute(
            "SELECT * FROM videos WHERE status = 'review' "
            "ORDER BY added_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_failed_downloads(self, limit: int = 20) -> builtins.list[Video]:
        rows = self.db.execute(
            "SELECT * FROM videos WHERE download_status = 'error' "
            "ORDER BY added_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    _EVICTABLE = (
        "storage_mode = 'cache' AND keep_forever = 0 "
        "AND download_status = 'ready' AND cached_at IS NOT NULL"
    )

    def list_expired_cache(self, cache_days: float) -> builtins.list[Video]:
        """Cached videos not played (or, if never played, not downloaded)
        within the last `cache_days`, oldest first. Uploads, keep_forever
        videos and anything mid-download are never eligible."""
        if cache_days <= 0:
            return []
        rows = self.db.execute(
            f"SELECT * FROM videos WHERE {self._EVICTABLE} "
            "AND COALESCE(last_played_at, cached_at) < datetime('now', ?) "
            "ORDER BY COALESCE(last_played_at, cached_at) ASC",
            (f"-{float(cache_days)} days",),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def is_hidden_for(self, video_id: str, profile_id: int) -> bool:
        """Whether this kid hid the video."""
        return self.db.execute(
            "SELECT 1 FROM profile_hidden_videos WHERE profile_id = ? AND video_id = ?",
            (profile_id, video_id)).fetchone() is not None

    def touch_played(self, video_id: str) -> None:
        self.db.execute(
            "UPDATE videos SET last_played_at = datetime('now'), "
            "play_count = play_count + 1 WHERE video_id = ?",
            (video_id,),
        )
        self.db.commit()

    def claim_evicted(self, video_id: str) -> bool:
        """Atomically flip an evicted video back to pending. Returns True
        for exactly one caller when several race to re-download it."""
        cur = self.db.execute(
            "UPDATE videos SET download_status = 'pending' "
            "WHERE video_id = ? AND download_status = 'evicted'",
            (video_id,),
        )
        self.db.commit()
        return cur.rowcount == 1

    def mark_evicted(self, video_id: str) -> None:
        """Mark a video whose files were deleted as 'evicted', clearing its
        cache metadata; the row itself is kept."""
        self.db.execute(
            "UPDATE videos "
            "SET download_status = 'evicted', "
            "    cached_at = NULL, "
            "    cache_expires_at = NULL, "
            "    file_size = 0 "
            "WHERE video_id = ?",
            (video_id,),
        )
        self.db.commit()

    def list_stuck_pending(self, older_than_hours: float = 1,
                           limit: int = 20) -> builtins.list[Video]:
        """Pending cached videos added more than `older_than_hours` ago.
        Compared with SQLite's clock because added_at is stored in UTC."""
        rows = self.db.execute(
            "SELECT * FROM videos WHERE download_status = 'pending' "
            "AND storage_mode = 'cache' "
            "AND added_at < datetime('now', ?) "
            "ORDER BY added_at ASC LIMIT ?",
            (f"-{older_than_hours} hours", limit),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_recent(self, limit: int = 5) -> builtins.list[Video]:
        rows = self.db.execute(
            "SELECT * FROM videos ORDER BY added_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def size_by_channel(self) -> builtins.list[tuple[int | None, str, int, int]]:
        """Return (channel_id, channel_name, video_count, total_bytes) per channel.

        Videos without a channel are grouped under channel_id=NULL, name='Unassigned'.
        Sorted by total_bytes descending.
        """
        rows = self.db.execute(
            "SELECT v.channel_id AS channel_id, "
            "COALESCE(c.name, 'Unassigned') AS channel_name, "
            "COUNT(*) AS video_count, "
            "COALESCE(SUM(v.file_size), 0) AS total_bytes "
            "FROM videos v LEFT JOIN channels c ON c.id = v.channel_id "
            "WHERE v.status = 'active' "
            "GROUP BY v.channel_id, c.name "
            "ORDER BY total_bytes DESC, channel_name ASC"
        ).fetchall()
        return [
            (r["channel_id"], r["channel_name"], int(r["video_count"]), int(r["total_bytes"]))
            for r in rows
        ]

    def _to_model(self, row: sqlite3.Row) -> Video:
        d = dict(row)
        return Video(
            id=d["id"],
            video_id=d["video_id"],
            extractor=d.get("extractor", ""),
            original_url=d.get("original_url", ""),
            source_id=d.get("source_id"),
            channel_id=d.get("channel_id"),
            title=d["title"],
            original_title=d.get("original_title", d["title"]),
            channel_name=d.get("channel_name", ""),
            description=d.get("description", ""),
            duration=d.get("duration", 0),
            upload_date=d.get("upload_date", ""),
            view_count=d.get("view_count", 0),
            thumbnail_url=d.get("thumbnail_url", ""),
            thumbnail_type=d.get("thumbnail_type", "original"),
            status=d.get("status", "active"),
            download_status=d.get("download_status", "pending"),
            download_error=d.get("download_error", ""),
            storage_mode=d.get("storage_mode", "cache"),
            resolution=d.get("resolution", "720p"),
            added_at=d.get("added_at", ""),
            cached_at=d.get("cached_at"),
            cache_expires_at=d.get("cache_expires_at"),
            file_size=d.get("file_size", 0),
            keep_forever=bool(d.get("keep_forever", 0)),
            last_played_at=d.get("last_played_at"),
            play_count=d.get("play_count") or 0,
            origin=d.get("origin") or "",
            origin_ref=d.get("origin_ref") or "",
            source_path=d.get("source_path") or "",
            episode_key=d.get("episode_key"),
        )
