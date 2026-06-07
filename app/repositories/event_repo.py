from __future__ import annotations
"""Event repository — usage event logging and statistics."""

import json
import sqlite3
from app.models import Event
from app.repositories.base import BaseRepository


class EventRepository(BaseRepository):

    def insert(self, event: Event) -> None:
        self.db.execute(
            "INSERT INTO events (profile_id, event_type, video_id, data_json) "
            "VALUES (?, ?, ?, ?)",
            (event.profile_id, event.event_type, event.video_id, event.data_json),
        )
        self.db.commit()

    def insert_raw(self, event_type: str, video_id: str | None,
                   profile_id: int | None, data_json: str = "{}") -> None:
        """Insert an event without constructing an Event model."""
        self.db.execute(
            "INSERT INTO events (event_type, video_id, profile_id, data_json) "
            "VALUES (?, ?, ?, ?)",
            (event_type, video_id, profile_id, data_json),
        )
        self.db.commit()

    def get_watch_time_today(self, profile_id: int | None = None) -> int:
        """Total watch seconds for today, optionally filtered by profile."""
        if profile_id is not None:
            row = self.db.execute(
                "SELECT COALESCE(SUM(json_extract(data_json, '$.watch_seconds')), 0) as total "
                "FROM events WHERE event_type = 'video_complete' "
                "AND date(timestamp) = date('now') AND profile_id = ?",
                (profile_id,),
            ).fetchone()
        else:
            row = self.db.execute(
                "SELECT COALESCE(SUM(json_extract(data_json, '$.watch_seconds')), 0) as total "
                "FROM events WHERE event_type = 'video_complete' "
                "AND date(timestamp) = date('now')"
            ).fetchone()
        return int(row["total"])

    def list_recent(self, limit: int = 50,
                    profile_id: int | None = None) -> list[Event]:
        if profile_id is not None:
            rows = self.db.execute(
                "SELECT e.*, v.title as video_title FROM events e "
                "LEFT JOIN videos v ON e.video_id = v.video_id "
                "WHERE e.profile_id = ? "
                "ORDER BY e.timestamp DESC LIMIT ?",
                (profile_id, limit),
            ).fetchall()
        else:
            rows = self.db.execute(
                "SELECT e.*, v.title as video_title FROM events e "
                "LEFT JOIN videos v ON e.video_id = v.video_id "
                "ORDER BY e.timestamp DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._to_model(r) for r in rows]

    # --- Aggregation queries for stats dashboard ---

    def count_events(self, event_type: str,
                     profile_id: int | None = None,
                     since: str | None = None,
                     video_id: str | None = None) -> int:
        sql = "SELECT COUNT(*) as n FROM events WHERE event_type = ?"
        params: list = [event_type]
        if profile_id is not None:
            sql += " AND profile_id = ?"
            params.append(profile_id)
        if video_id is not None:
            sql += " AND video_id = ?"
            params.append(video_id)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        return int(self.db.execute(sql, params).fetchone()["n"])

    def count_completions(self, profile_id: int | None = None,
                          since: str | None = None) -> int:
        return self.count_events("video_complete", profile_id=profile_id,
                                 since=since)

    def watch_seconds(self, profile_id: int | None = None,
                      since: str | None = None) -> int:
        sql = ("SELECT COALESCE(SUM(json_extract(data_json, '$.watch_seconds')), 0) as total "
               "FROM events WHERE event_type = 'video_complete'")
        params: list = []
        if profile_id is not None:
            sql += " AND profile_id = ?"
            params.append(profile_id)
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        return int(self.db.execute(sql, params).fetchone()["total"])

    def top_videos_by_completions(self, profile_id: int | None = None,
                                  since: str | None = None,
                                  limit: int = 10) -> list[dict]:
        sql = ("SELECT e.video_id, "
               "COUNT(*) as plays, "
               "COALESCE(SUM(json_extract(e.data_json, '$.watch_seconds')), 0) as secs, "
               "v.title as video_title "
               "FROM events e "
               "LEFT JOIN videos v ON e.video_id = v.video_id "
               "WHERE e.event_type = 'video_complete'")
        params: list = []
        if profile_id is not None:
            sql += " AND e.profile_id = ?"
            params.append(profile_id)
        if since:
            sql += " AND e.timestamp >= ?"
            params.append(since)
        sql += " GROUP BY e.video_id ORDER BY plays DESC LIMIT ?"
        params.append(limit)
        rows = self.db.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def stats_per_profile(self, since: str | None = None) -> list[dict]:
        sql = ("SELECT e.profile_id, "
               "COALESCE(p.display_name, p.name, 'Unknown') as profile_name, "
               "COALESCE(p.avatar, 'default') as avatar, "
               "COUNT(*) as completions, "
               "COALESCE(SUM(json_extract(e.data_json, '$.watch_seconds')), 0) as secs "
               "FROM events e "
               "LEFT JOIN profiles p ON e.profile_id = p.id "
               "WHERE e.event_type = 'video_complete' AND e.profile_id IS NOT NULL")
        params: list = []
        if since:
            sql += " AND e.timestamp >= ?"
            params.append(since)
        sql += " GROUP BY e.profile_id ORDER BY completions DESC"
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def last_watched_video_id(self, profile_id: int) -> str | None:
        """Most-recent video a profile completed or opened. Returns just
        the video_id; callers resolve the full Video row themselves."""
        row = self.db.execute(
            "SELECT video_id FROM events "
            "WHERE profile_id = ? "
            "AND event_type IN ('video_complete', 'video_view', 'video_play') "
            "AND video_id IS NOT NULL "
            "ORDER BY timestamp DESC LIMIT 1",
            (profile_id,),
        ).fetchone()
        return row["video_id"] if row else None

    def video_stats(self, video_id: str, since: str | None = None) -> dict:
        sql = ("SELECT COUNT(*) as completions, "
               "COALESCE(SUM(json_extract(data_json, '$.watch_seconds')), 0) as secs, "
               "COUNT(DISTINCT profile_id) as unique_watchers "
               "FROM events "
               "WHERE event_type = 'video_complete' AND video_id = ?")
        params: list = [video_id]
        if since:
            sql += " AND timestamp >= ?"
            params.append(since)
        return dict(self.db.execute(sql, params).fetchone())

    def _to_model(self, row: sqlite3.Row) -> Event:
        d = dict(row)
        return Event(
            id=d["id"],
            profile_id=d.get("profile_id"),
            event_type=d["event_type"],
            video_id=d.get("video_id"),
            timestamp=d.get("timestamp", ""),
            data_json=d.get("data_json", "{}"),
            video_title=d.get("video_title"),
        )
