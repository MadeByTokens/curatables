"""Import jobs: one row per file, draft → queued → working → outcome."""

from __future__ import annotations

import builtins
import json
from dataclasses import dataclass, field

from app.repositories.base import BaseRepository

ACTIVE_STATES = ("queued", "working", "cancelling")
FINISHED_STATES = ("done", "failed", "cancelled")


@dataclass
class ImportJob:
    id: int
    batch: str
    source_path: str
    source_size: int = 0
    source_mtime: float = 0.0
    details: dict = field(default_factory=dict)
    has_picture: bool = False
    mode: str = "copy"
    keep_original: bool = True
    state: str = "draft"
    phase: str = ""
    done: float = 0.0
    total: float = 0.0
    speed: float | None = None
    eta_seconds: float | None = None
    stalled_for: float = 0.0
    error: str = ""
    video_id: str = ""
    kind: str = ""
    media_seconds: float = 0.0
    wall_seconds: float = 0.0
    created_at: str = ""
    updated_at: str = ""
    finished_at: str | None = None

    @property
    def name(self) -> str:
        return self.source_path.rsplit("/", 1)[-1]

    @property
    def title(self) -> str:
        return str(self.details.get("title") or self.name)

    @property
    def fraction(self) -> float | None:
        if self.total <= 0:
            return None
        return max(0.0, min(1.0, self.done / self.total))


_COLUMNS = ("id, batch, source_path, source_size, source_mtime, details, "
            "picture IS NOT NULL AS has_picture, mode, keep_original, state, phase, done, "
            "total, speed, eta_seconds, stalled_for, error, video_id, kind, media_seconds, "
            "wall_seconds, created_at, updated_at, finished_at")


class ImportJobRepository(BaseRepository):

    def _to_model(self, row) -> ImportJob:
        d = dict(row)
        d["details"] = json.loads(d.get("details") or "{}")
        d["has_picture"] = bool(d.get("has_picture"))
        d["keep_original"] = bool(d.get("keep_original"))
        return ImportJob(**d)

    def create_draft(self, batch: str, source_path: str, size: int, mtime: float,
                     details: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO import_jobs (batch, source_path, source_size, source_mtime, details) "
            "VALUES (?, ?, ?, ?, ?)",
            (batch, source_path, size, mtime, json.dumps(details)))
        self.db.commit()
        return self._new_id(cur)

    def get(self, job_id: int) -> ImportJob | None:
        row = self.db.execute(
            f"SELECT {_COLUMNS} FROM import_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._to_model(row) if row else None

    def picture(self, job_id: int) -> bytes | None:
        row = self.db.execute("SELECT picture FROM import_jobs WHERE id = ?",
                              (job_id,)).fetchone()
        return bytes(row["picture"]) if row and row["picture"] is not None else None

    def list_batch(self, batch: str) -> builtins.list[ImportJob]:
        rows = self.db.execute(
            f"SELECT {_COLUMNS} FROM import_jobs WHERE batch = ? ORDER BY source_path, id",
            (batch,)).fetchall()
        return [self._to_model(r) for r in rows]

    def list_states(self, states: tuple[str, ...], limit: int = 200
                    ) -> builtins.list[ImportJob]:
        marks = ",".join("?" for _ in states)
        rows = self.db.execute(
            f"SELECT {_COLUMNS} FROM import_jobs WHERE state IN ({marks}) "
            "ORDER BY CASE state WHEN 'working' THEN 0 WHEN 'cancelling' THEN 0 "
            "WHEN 'queued' THEN 1 ELSE 2 END, "
            "COALESCE(finished_at, '') DESC, id LIMIT ?",
            (*states, limit)).fetchall()
        return [self._to_model(r) for r in rows]

    def count_active(self) -> int:
        marks = ",".join("?" for _ in ACTIVE_STATES)
        row = self.db.execute(
            f"SELECT COUNT(*) AS n FROM import_jobs WHERE state IN ({marks})",
            ACTIVE_STATES).fetchone()
        return int(row["n"])

    def next_queued(self) -> ImportJob | None:
        row = self.db.execute(
            f"SELECT {_COLUMNS} FROM import_jobs WHERE state = 'queued' ORDER BY id LIMIT 1"
        ).fetchone()
        return self._to_model(row) if row else None

    def update_details(self, job_id: int, details: dict) -> None:
        self.db.execute(
            "UPDATE import_jobs SET details = ?, updated_at = datetime('now') WHERE id = ?",
            (json.dumps(details), job_id))
        self.db.commit()

    def set_picture(self, job_id: int, data: bytes | None) -> None:
        self.db.execute("UPDATE import_jobs SET picture = ? WHERE id = ?", (data, job_id))
        self.db.commit()

    def queue_batch(self, batch: str, mode: str, keep_original: bool) -> int:
        cur = self.db.execute(
            "UPDATE import_jobs SET state = 'queued', mode = ?, keep_original = ?, "
            "updated_at = datetime('now') WHERE batch = ? AND state = 'draft'",
            (mode, int(keep_original), batch))
        self.db.commit()
        return cur.rowcount

    def set_mode(self, job_id: int, mode: str) -> None:
        self.db.execute("UPDATE import_jobs SET mode = ? WHERE id = ?", (mode, job_id))
        self.db.commit()

    def claim(self, job_id: int) -> bool:
        """queued → working; False if someone cancelled it first."""
        cur = self.db.execute(
            "UPDATE import_jobs SET state = 'working', phase = '', done = 0, total = 0, "
            "error = '', updated_at = datetime('now') WHERE id = ? AND state = 'queued'",
            (job_id,))
        self.db.commit()
        return cur.rowcount == 1

    def progress(self, job_id: int, phase: str, done: float, total: float,
                 speed: float | None, eta_seconds: float | None, stalled_for: float) -> None:
        self.db.execute(
            "UPDATE import_jobs SET phase = ?, done = ?, total = ?, speed = ?, "
            "eta_seconds = ?, stalled_for = ?, updated_at = datetime('now') WHERE id = ?",
            (phase, done, total, speed, eta_seconds, stalled_for, job_id))
        self.db.commit()

    def request_cancel(self, job_id: int) -> str | None:
        """Returns the state the job was in, or None if it was not active."""
        job = self.get(job_id)
        if job is None:
            return None
        if job.state == "queued" or job.state == "draft":
            self.finish(job_id, "cancelled")
            return job.state
        if job.state == "working":
            self.db.execute(
                "UPDATE import_jobs SET state = 'cancelling', updated_at = datetime('now') "
                "WHERE id = ? AND state = 'working'", (job_id,))
            self.db.commit()
            return job.state
        return None

    def finish(self, job_id: int, state: str, error: str = "", video_id: str = "",
               kind: str = "", media_seconds: float = 0.0, wall_seconds: float = 0.0) -> None:
        self.db.execute(
            "UPDATE import_jobs SET state = ?, error = ?, video_id = ?, kind = ?, "
            "media_seconds = ?, wall_seconds = ?, picture = NULL, speed = NULL, "
            "eta_seconds = NULL, stalled_for = 0, updated_at = datetime('now'), "
            "finished_at = datetime('now') WHERE id = ?",
            (state, error, video_id, kind, media_seconds, wall_seconds, job_id))
        self.db.commit()

    def recover_after_restart(self) -> None:
        """A restart interrupted whatever was running: run it again, but
        honour a cancel that was already asked for."""
        self.db.execute(
            "UPDATE import_jobs SET state = 'queued', phase = '', done = 0, total = 0 "
            "WHERE state = 'working'")
        self.db.execute(
            "UPDATE import_jobs SET state = 'cancelled', finished_at = datetime('now') "
            "WHERE state = 'cancelling'")
        self.db.commit()

    def delete_stale_drafts(self, hours: int = 24) -> builtins.list[int]:
        rows = self.db.execute(
            "SELECT id FROM import_jobs WHERE state = 'draft' "
            "AND updated_at < datetime('now', ?)", (f"-{int(hours)} hours",)).fetchall()
        ids = [int(r["id"]) for r in rows]
        if ids:
            marks = ",".join("?" for _ in ids)
            self.db.execute(f"DELETE FROM import_jobs WHERE id IN ({marks})", ids)
            self.db.commit()
        return ids

    def delete_drafts(self, batch: str) -> None:
        self.db.execute("DELETE FROM import_jobs WHERE batch = ? AND state = 'draft'",
                        (batch,))
        self.db.commit()

    def clear_finished(self) -> None:
        marks = ",".join("?" for _ in FINISHED_STATES)
        self.db.execute(f"DELETE FROM import_jobs WHERE state IN ({marks})", FINISHED_STATES)
        self.db.commit()

    def measured_speed(self, kind: str) -> float | None:
        """Media seconds per wall second over past conversions of this kind."""
        row = self.db.execute(
            "SELECT SUM(media_seconds) AS m, SUM(wall_seconds) AS w FROM import_jobs "
            "WHERE state = 'done' AND kind = ? AND wall_seconds > 0", (kind,)).fetchone()
        if not row or not row["w"]:
            return None
        return float(row["m"]) / float(row["w"])

    def imported_paths(self, paths: builtins.list[str]) -> set[str]:
        """Which of these files are already in the library."""
        if not paths:
            return set()
        found: set[str] = set()
        for i in range(0, len(paths), 500):
            chunk = paths[i:i + 500]
            marks = ",".join("?" for _ in chunk)
            rows = self.db.execute(
                f"SELECT source_path FROM videos WHERE source_path IN ({marks})",
                chunk).fetchall()
            found.update(r["source_path"] for r in rows)
        return found
