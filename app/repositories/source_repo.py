"""Source repository: upstream channels, playlists and single videos.

A source can come from any platform yt-dlp supports, so uniqueness is
`(extractor, external_id)`: the same raw ID may exist on two platforms.
"""

from __future__ import annotations

import sqlite3

from app.models import Source
from app.repositories.base import BaseRepository


class SourceRepository(BaseRepository):

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
            return self._new_id(cur)
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT id FROM sources WHERE extractor = ? AND external_id = ?",
                (source.extractor, source.external_id),
            ).fetchone()
            return int(row["id"])
