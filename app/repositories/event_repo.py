"""Event repository: usage event logging and statistics."""

from __future__ import annotations


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

    def video_activity(self, profile_id: int | None = None,
                       since: str | None = None) -> list[dict]:
        """Per video: completions ("plays"), seconds in them and opens."""
        sql = ("SELECT e.video_id, "
               "SUM(e.event_type = 'video_complete') as plays, "
               "COALESCE(SUM(CASE WHEN e.event_type = 'video_complete' "
               "THEN json_extract(e.data_json, '$.watch_seconds') END), 0) as secs, "
               "SUM(e.event_type = 'video_view') as opens, "
               "v.title as video_title "
               "FROM events e "
               "LEFT JOIN videos v ON e.video_id = v.video_id "
               "WHERE e.event_type IN ('video_complete', 'video_view') "
               "AND e.video_id IS NOT NULL")
        params: list = []
        if profile_id is not None:
            sql += " AND e.profile_id = ?"
            params.append(profile_id)
        if since:
            sql += " AND e.timestamp >= ?"
            params.append(since)
        sql += " GROUP BY e.video_id"
        return [dict(r) for r in self.db.execute(sql, params).fetchall()]

    def stats_per_profile(self, since: str | None = None) -> list[dict]:
        sql = ("SELECT e.profile_id, "
               "COALESCE(NULLIF(p.display_name, ''), p.name, 'Unknown') as profile_name, "
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

    def kid_uploaders(self, video_ids: list[str]) -> dict[str, str]:
        """{video_id: kid's name} for videos a kid uploaded (while the
        upload event is still kept)."""
        if not video_ids:
            return {}
        placeholders = ",".join("?" for _ in video_ids)
        rows = self.db.execute(
            "SELECT e.video_id, COALESCE(NULLIF(p.display_name, ''), p.name) as name "
            "FROM events e JOIN profiles p ON p.id = e.profile_id "
            f"WHERE e.event_type = 'video_uploaded_by_kid' AND e.video_id IN ({placeholders})",
            video_ids).fetchall()
        return {r["video_id"]: r["name"] for r in rows}

    def kid_upload_history(self, profile_id: int, limit: int = 8) -> list[dict]:
        """The kid's latest uploads, newest first: video_id and the title
        they gave it. One the kid took back since is left out."""
        rows = self.db.execute(
            "SELECT video_id, MAX(timestamp) AS at, "
            "json_extract(data_json, '$.title') AS title FROM events u "
            "WHERE event_type = 'video_uploaded_by_kid' AND profile_id = ? "
            "AND video_id IS NOT NULL GROUP BY video_id "
            "HAVING MAX(id) > COALESCE((SELECT MAX(c.id) FROM events c "
            "WHERE c.event_type = 'kid_upload_taken_back' AND c.profile_id = u.profile_id "
            "AND c.video_id = u.video_id), 0) "
            "ORDER BY at DESC LIMIT ?",
            (profile_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def kid_uploaded(self, profile_id: int, video_id: str) -> bool:
        """Whether this kid uploaded the video (a duplicate of a video
        already here logs nothing, so it never counts)."""
        row = self.db.execute(
            "SELECT 1 FROM events WHERE event_type = 'video_uploaded_by_kid' "
            "AND profile_id = ? AND video_id = ? LIMIT 1", (profile_id, video_id)).fetchone()
        return row is not None

    def last_watched_video_id(self, profile_id: int) -> str | None:
        """ID of the video this profile most recently opened or completed."""
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

    def delete_older_than(self, days: float) -> int:
        cur = self.db.execute(
            "DELETE FROM events WHERE timestamp < datetime('now', ?)",
            (f"-{float(days)} days",),
        )
        self.db.commit()
        return cur.rowcount
