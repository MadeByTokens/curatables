"""Upload service — tus.io-backed parent video uploads.

Handles the lifecycle of a single uploaded video file:
  create → append (many) → finalize → Video row

In-progress uploads live under `{media_dir}/uploads/.tmp/` as a pair
of files: the bytes file `{token}` and a JSON sidecar `{token}.json`.
On finalize we hash the file, compute the upload video_id, move the
bytes into `{media_dir}/uploads/up_<hash[:16]>/video.<ext>`, run
ffprobe validation, extract a thumbnail, and insert the videos row.
"""

from __future__ import annotations

import json
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, asdict
from pathlib import Path

from app.models import Video
from app.repositories import VideoRepository
from app.services.ingest import IngestService, VideoDetails, upload_video_id
from app.services.storage import StorageService
from app.services.media_probe import MediaProbeService, UnsupportedCodec, ProbeError
from app.services.thumbnails import ThumbnailService

logger = logging.getLogger(__name__)


_ALLOWED_EXTS = {"mp4", "mkv", "webm", "mov", "m4v", "avi", "mpg", "mpeg"}


class UploadError(Exception):
    """Raised when an upload operation fails in a way the client should see."""


class UploadNotFound(UploadError):
    pass


class UploadOffsetMismatch(UploadError):
    pass


class DuplicateUpload(UploadError):
    """The file is already in the library, in a different channel."""

    def __init__(self, video: Video):
        super().__init__(f"Already in the library as {video.video_id}")
        self.video = video


@dataclass
class UploadSession:
    token: str
    filename: str
    total_size: int
    channel_id: int
    title: str
    started_at: float


class UploadService:
    def __init__(self,
                 storage: StorageService,
                 probe: MediaProbeService,
                 video_repo: VideoRepository,
                 thumbnails: ThumbnailService):
        self.storage = storage
        self.probe = probe
        self.video_repo = video_repo
        self.thumbnails = thumbnails
        self.ingest = IngestService(storage, probe, video_repo, thumbnails)

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def create(self, filename: str, total_size: int,
               channel_id: int, title: str) -> UploadSession:
        tmp_dir = self.storage.upload_tmp_dir()
        token = secrets.token_urlsafe(24)
        session = UploadSession(
            token=token,
            filename=filename,
            total_size=int(total_size),
            channel_id=int(channel_id),
            title=title or "",
            started_at=time.time(),
        )
        (tmp_dir / token).touch()
        sidecar = tmp_dir / f"{token}.json"
        sidecar.write_text(json.dumps(asdict(session)))
        return session

    def load_session(self, token: str) -> UploadSession:
        sidecar = self.storage.upload_tmp_dir() / f"{token}.json"
        if not sidecar.exists():
            raise UploadNotFound(f"Unknown upload token: {token}")
        data = json.loads(sidecar.read_text())
        return UploadSession(**data)

    def get_offset(self, token: str) -> int:
        path = self.storage.upload_tmp_dir() / token
        if not path.exists():
            raise UploadNotFound(f"Unknown upload token: {token}")
        return path.stat().st_size

    def append(self, token: str, expected_offset: int, chunk: bytes) -> int:
        path = self.storage.upload_tmp_dir() / token
        if not path.exists():
            raise UploadNotFound(f"Unknown upload token: {token}")
        current = path.stat().st_size
        if current != expected_offset:
            raise UploadOffsetMismatch(
                f"Upload offset mismatch: client sent {expected_offset}, "
                f"server has {current}"
            )
        total = self.load_session(token).total_size
        if current + len(chunk) > total:
            raise UploadOffsetMismatch(
                f"Chunk would exceed the declared upload length ({total} bytes)")
        with open(path, "ab") as f:
            f.write(chunk)
        return path.stat().st_size

    def cancel(self, token: str) -> None:
        tmp_dir = self.storage.upload_tmp_dir()
        (tmp_dir / token).unlink(missing_ok=True)
        (tmp_dir / f"{token}.json").unlink(missing_ok=True)

    def finalize(self, token: str, status: str = "active") -> Video:
        """Validate, move, and register a completed upload.

        On UnsupportedCodec or validation failure, the tmp file and
        sidecar are deleted before the exception propagates so that a
        failed upload does not leave orphans behind.
        """
        session = self.load_session(token)
        tmp_path = self.storage.upload_tmp_dir() / token
        if not tmp_path.exists():
            raise UploadNotFound(f"Upload bytes missing for token {token}")

        received = tmp_path.stat().st_size
        if received != session.total_size:
            self.cancel(token)
            raise UploadError(
                f"Upload is incomplete: {received} of "
                f"{session.total_size} bytes received"
            )

        ext = self._pick_extension(session.filename)

        # Validate while still in .tmp so a bad upload never reaches uploads/.
        try:
            probe = self.probe.validate(tmp_path, original_filename=session.filename)
        except (UnsupportedCodec, ProbeError):
            self.cancel(token)
            raise

        video_id = upload_video_id(self.ingest.hash_file(tmp_path))

        # Same bytes already uploaded: keep the existing video.
        existing = self.video_repo.get(video_id)
        if existing is not None:
            self.cancel(token)
            if existing.channel_id != session.channel_id:
                raise DuplicateUpload(existing)
            return existing

        # Normalize to the playback baseline inside a work folder, as
        # downloads are.
        work = self.ingest.work_dir(f"{token}.work")
        staged = work / f"video.{ext}"
        tmp_path.replace(staged)
        (self.storage.upload_tmp_dir() / f"{token}.json").unlink(missing_ok=True)
        try:
            staged, probe, _ = self.ingest.normalize(staged, probe)
        except BaseException:
            shutil.rmtree(work, ignore_errors=True)
            raise

        title = session.title.strip() or self._title_from_filename(session.filename)
        return self.ingest.commit(work, staged, video_id, probe, VideoDetails(
            title=title, channel_id=session.channel_id, status=status))

    def sweep_abandoned(self, ttl_hours: int = 24) -> int:
        """Delete tmp upload files older than ttl_hours. Returns count deleted."""
        return self.storage.sweep_upload_tmp(ttl_hours)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _pick_extension(filename: str) -> str:
        suffix = Path(filename).suffix.lstrip(".").lower()
        if suffix in _ALLOWED_EXTS:
            return suffix
        return "mp4"

    @staticmethod
    def _title_from_filename(filename: str) -> str:
        stem = Path(filename).stem
        cleaned = stem.replace("_", " ").replace("-", " ").strip()
        return cleaned or "Untitled upload"
