from __future__ import annotations
"""Video repository — all video-related SQL."""

import sqlite3
from app.models import Video
from app.repositories.base import BaseRepository


class VideoRepository(BaseRepository):

    def get(self, video_id: str) -> Video | None:
        row = self.db.execute(
            "SELECT * FROM videos WHERE video_id = ?", (video_id,)
        ).fetchone()
        if not row:
            return None
        return self._to_model(row)

    def list(self, status: str | None = "active",
             channel_id: int | None = None,
             limit: int = 30, offset: int = 0) -> list[Video]:
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
            f"SELECT * FROM videos {where} ORDER BY added_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_by_channels(self, channel_ids: list[int],
                         limit: int = 30, offset: int = 0) -> list[Video]:
        if not channel_ids:
            return []
        placeholders = ",".join("?" for _ in channel_ids)
        params: list = list(channel_ids)
        params.extend([limit, offset])
        rows = self.db.execute(
            f"SELECT * FROM videos WHERE status = 'active' "
            f"AND channel_id IN ({placeholders}) "
            f"ORDER BY added_at DESC LIMIT ? OFFSET ?",
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
        return row["cnt"]

    def list_ready(self, channel_id: int | None = None,
                   limit: int = 30, offset: int = 0) -> list[Video]:
        """List videos that are active AND downloaded (visible to kids)."""
        clauses = ["status = 'active'", "download_status = 'ready'"]
        params: list = []
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        where = "WHERE " + " AND ".join(clauses)
        params.extend([limit, offset])
        rows = self.db.execute(
            f"SELECT * FROM videos {where} ORDER BY added_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_ready(self, channel_id: int | None = None) -> int:
        clauses = ["status = 'active'", "download_status = 'ready'"]
        params: list = []
        if channel_id is not None:
            clauses.append("channel_id = ?")
            params.append(channel_id)
        where = "WHERE " + " AND ".join(clauses)
        row = self.db.execute(
            f"SELECT COUNT(*) as cnt FROM videos {where}", params
        ).fetchone()
        return row["cnt"]

    def list_ready_by_channels(self, channel_ids: list[int],
                               limit: int = 30, offset: int = 0) -> list[Video]:
        if not channel_ids:
            return []
        placeholders = ",".join("?" for _ in channel_ids)
        params: list = list(channel_ids)
        params.extend([limit, offset])
        rows = self.db.execute(
            f"SELECT * FROM videos WHERE status = 'active' AND download_status = 'ready' "
            f"AND channel_id IN ({placeholders}) "
            f"ORDER BY added_at DESC LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_ready_by_channels(self, channel_ids: list[int]) -> int:
        if not channel_ids:
            return 0
        placeholders = ",".join("?" for _ in channel_ids)
        row = self.db.execute(
            f"SELECT COUNT(*) as cnt FROM videos "
            f"WHERE status = 'active' AND download_status = 'ready' "
            f"AND channel_id IN ({placeholders})",
            channel_ids,
        ).fetchone()
        return row["cnt"]

    def search_ready(self, query: str, channel_ids: list[int] | None = None,
                     limit: int = 24, offset: int = 0) -> list[Video]:
        """Search within ready videos by title and description."""
        like = f"%{query}%"
        if channel_ids is not None and len(channel_ids) > 0:
            placeholders = ",".join("?" for _ in channel_ids)
            params: list = list(channel_ids)
            params.extend([like, like, limit, offset])
            rows = self.db.execute(
                f"SELECT * FROM videos WHERE status = 'active' AND download_status = 'ready' "
                f"AND channel_id IN ({placeholders}) "
                f"AND (title LIKE ? OR description LIKE ?) "
                f"ORDER BY added_at DESC LIMIT ? OFFSET ?",
                params,
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM videos WHERE status = 'active' AND download_status = 'ready' "
                "AND (title LIKE ? OR description LIKE ?) "
                "ORDER BY added_at DESC LIMIT ? OFFSET ?",
                (like, like, limit, offset),
            ).fetchall()
        return [self._to_model(r) for r in rows]

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

    def list_pending(self) -> list[Video]:
        """Videos stuck in 'pending' — used by the startup resume path
        that kicks off downloads orphaned by a server restart."""
        rows = self.db.execute(
            "SELECT * FROM videos WHERE download_status = 'pending' "
            "AND storage_mode = 'cache' "
            "ORDER BY added_at ASC"
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_failed_downloads(self, limit: int = 20) -> list[Video]:
        rows = self.db.execute(
            "SELECT * FROM videos WHERE download_status = 'error' "
            "ORDER BY added_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_expired_cache(self, cache_days: float,
                           now_iso: str | None = None) -> list[Video]:
        """Videos whose cached_at is older than `cache_days` and are
        eligible for eviction.

        Eligibility:
          - storage_mode = 'cache' (uploads are never auto-evicted)
          - keep_forever = 0       (library-mode videos are pinned)
          - download_status = 'ready' (don't touch in-flight downloads)
          - cached_at IS NOT NULL AND cached_at < cutoff

        `cache_days` is a float so tests can pass fractional values
        (e.g. 0.0001 ≈ 8 seconds) without fighting datetime math.
        `now_iso` lets tests inject a deterministic cutoff; production
        omits it and SQLite uses `datetime('now')` (UTC).
        """
        if cache_days <= 0:
            return []
        if now_iso is None:
            # Use SQLite's own clock so the comparison stays in the
            # same UTC baseline as added_at / cached_at defaults.
            cutoff_expr = f"datetime('now', '-{float(cache_days)} days')"
            rows = self.db.execute(
                "SELECT * FROM videos "
                "WHERE storage_mode = 'cache' "
                "AND keep_forever = 0 "
                "AND download_status = 'ready' "
                "AND cached_at IS NOT NULL "
                f"AND cached_at < {cutoff_expr} "
                "ORDER BY cached_at ASC"
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT * FROM videos "
                "WHERE storage_mode = 'cache' "
                "AND keep_forever = 0 "
                "AND download_status = 'ready' "
                "AND cached_at IS NOT NULL "
                "AND cached_at < datetime(?, ?) "
                "ORDER BY cached_at ASC",
                (now_iso, f"-{float(cache_days)} days"),
            ).fetchall()
        return [self._to_model(r) for r in rows]

    def mark_evicted(self, video_id: str) -> None:
        """Transition a ready video to 'evicted' — files gone, row kept.
        Clears cached_at / cache_expires_at / file_size in the same
        statement so the row's cache-metadata columns stay consistent."""
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
                           limit: int = 20) -> list[Video]:
        """Pending videos whose added_at is more than `older_than_hours`
        in the past. Uses SQLite datetime arithmetic so the comparison
        stays in UTC (videos.added_at defaults to datetime('now'), which
        is UTC; Python's datetime.now() is local and would drift)."""
        rows = self.db.execute(
            "SELECT * FROM videos WHERE download_status = 'pending' "
            "AND storage_mode = 'cache' "
            "AND added_at < datetime('now', ?) "
            "ORDER BY added_at ASC LIMIT ?",
            (f"-{older_than_hours} hours", limit),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def list_recent(self, limit: int = 5) -> list[Video]:
        rows = self.db.execute(
            "SELECT * FROM videos ORDER BY added_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def size_by_channel(self) -> list[tuple[int | None, str, int, int]]:
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
        )
