from __future__ import annotations
"""Source repository — upstream content sources (channels, playlists, videos).

A Source row represents a channel, playlist, or single-video URL on
some external video platform — YouTube, Vimeo, Peertube, whatever
yt-dlp can handle. Uniqueness is `(extractor, external_id)`, so the
same raw ID can legitimately appear under two different platforms.
"""

import sqlite3
from app.models import Source
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository):

    def get(self, source_id: int) -> Source | None:
        row = self.db.execute(
            "SELECT * FROM sources WHERE id = ?", (source_id,)
        ).fetchone()
        if not row:
            return None
        return self._to_model(row)

    def get_by_external_id(self, extractor: str,
                           external_id: str) -> Source | None:
        row = self.db.execute(
            "SELECT * FROM sources WHERE extractor = ? AND external_id = ?",
            (extractor, external_id),
        ).fetchone()
        if not row:
            return None
        return self._to_model(row)

    def list(self) -> list[Source]:
        rows = self.db.execute(
            "SELECT * FROM sources ORDER BY added_at DESC"
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def create(self, source: Source) -> int:
        """Insert a source. Returns ID (existing if duplicate extractor+external_id)."""
        try:
            cur = self.db.execute(
                "INSERT INTO sources (source_type, extractor, external_id, "
                "title, description, url, auto_sync, metadata_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    source.source_type, source.extractor, source.external_id,
                    source.title, source.description, source.url,
                    int(source.auto_sync), source.metadata_json,
                ),
            )
            self.db.commit()
            return cur.lastrowid
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT id FROM sources WHERE extractor = ? AND external_id = ?",
                (source.extractor, source.external_id),
            ).fetchone()
            return row["id"]

    def delete(self, source_id: int) -> None:
        self.db.execute("DELETE FROM sources WHERE id = ?", (source_id,))
        self.db.commit()

    def _to_model(self, row: sqlite3.Row) -> Source:
        d = dict(row)
        return Source(
            id=d["id"],
            source_type=d["source_type"],
            extractor=d.get("extractor", "youtube"),
            external_id=d["external_id"],
            title=d["title"],
            url=d["url"],
            description=d.get("description", ""),
            auto_sync=bool(d.get("auto_sync", 0)),
            status=d.get("status", "active"),
            metadata_json=d.get("metadata_json", "{}"),
            added_at=d.get("added_at", ""),
        )
