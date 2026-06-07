from __future__ import annotations
"""Upload service — tus.io-backed parent video uploads.

Handles the lifecycle of a single uploaded video file:
  create → append (many) → finalize → Video row

In-progress uploads live under `{data_dir}/uploads/.tmp/` as a pair
of files: the bytes file `{token}` and a JSON sidecar `{token}.json`.
On finalize we hash the file, compute the upload video_id, move the
bytes into `{data_dir}/uploads/up_<hash[:16]>/video.<ext>`, run
ffprobe validation, extract a thumbnail, and insert the videos row.
"""

import hashlib
import json
import logging
import secrets
import shutil
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path

from app.models import Video
from app.repositories import VideoRepository, ChannelRepository
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.media_probe import MediaProbeService, UnsupportedCodec, ProbeError
from app.services.thumbnails import ThumbnailService

logger = logging.getLogger(__name__)


_HASH_CHUNK = 1024 * 1024
_ALLOWED_EXTS = {"mp4", "mkv", "webm", "mov", "m4v", "avi", "mpg", "mpeg"}


class UploadError(Exception):
    """Raised when an upload operation fails in a way the client should see."""


class UploadNotFound(UploadError):
    pass


class UploadOffsetMismatch(UploadError):
    pass


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
                 channel_repo: ChannelRepository,
                 thumbnails: ThumbnailService):
        self.storage = storage
        self.probe = probe
        self.video_repo = video_repo
        self.channel_repo = channel_repo
        self.thumbnails = thumbnails

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
        # Create the empty bytes file and the sidecar atomically enough
        # that a partial create is easy to sweep later.
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
        with open(path, "ab") as f:
            f.write(chunk)
        return path.stat().st_size

    def cancel(self, token: str) -> None:
        tmp_dir = self.storage.upload_tmp_dir()
        (tmp_dir / token).unlink(missing_ok=True)
        (tmp_dir / f"{token}.json").unlink(missing_ok=True)

    def finalize(self, token: str) -> Video:
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

        # Probe and validate first, while the file is still in .tmp —
        # this way a bad upload never contaminates the uploads/ tree.
        try:
            probe = self.probe.validate(tmp_path, original_filename=session.filename)
        except (UnsupportedCodec, ProbeError):
            self.cancel(token)
            raise

        # Compute content hash and derive the video_id.
        digest = self._hash_file(tmp_path)
        video_id = f"up_{digest[:16]}"

        # Dedup: if this exact file was already uploaded, drop the new
        # bytes and return the existing video.
        existing = self.video_repo.get(video_id)
        if existing is not None:
            self.cancel(token)
            return existing

        # Move to the final location.
        target_dir = self.storage.upload_video_dir(video_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"video.{ext}"
        shutil.move(str(tmp_path), str(target_path))
        (self.storage.upload_tmp_dir() / f"{token}.json").unlink(missing_ok=True)

        # Bring the upload into the client playback baseline, exactly as
        # downloaded videos are normalized. validate() above already
        # guaranteed the server can decode it; here we transcode/remux
        # 1080p, H.265, VP9, Opus, non-faststart uploads down to
        # H.264/AAC ≤720p30 +faststart so they play on old devices.
        if self.storage.normalizer is not None:
            norm = self.storage.normalizer.normalize(target_path)
            target_path = norm.path
            if norm.action in ("transcode", "remux"):
                try:
                    probe = self.probe.probe(target_path)  # refresh height/duration
                except ProbeError:
                    pass  # keep the pre-normalize probe values

        # Extract a thumbnail from the newly placed file.
        try:
            self.thumbnails.extract_frame(video_id, source_path=target_path)
        except Exception as e:
            logger.warning("Thumbnail extract failed for %s: %s", video_id, e)

        # Derive a user-facing title.
        title = session.title.strip() or self._title_from_filename(session.filename)

        # Build and insert the videos row.
        now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        video = Video(
            video_id=video_id,
            title=title,
            original_title=title,
            channel_name="",
            description="",
            duration=int(probe.duration_seconds or 0),
            upload_date=now[:10],
            view_count=0,
            thumbnail_url="",
            thumbnail_type="frame",
            status="active",
            download_status="ready",
            storage_mode="uploaded",
            resolution=f"{probe.height}p" if probe.height else "",
            source_id=None,
            channel_id=session.channel_id,
            file_size=target_path.stat().st_size,
        )
        self.video_repo.insert(video)
        self.video_repo.update(
            video_id,
            cached_at=now,
            file_size=video.file_size,
        )
        inserted = self.video_repo.get(video_id)
        return inserted or video

    def sweep_abandoned(self, ttl_hours: int = 24) -> int:
        """Delete tmp upload files older than ttl_hours. Returns count deleted."""
        tmp_dir = self.storage.upload_tmp_dir()
        if not tmp_dir.exists():
            return 0
        cutoff = time.time() - ttl_hours * 3600
        removed = 0
        for entry in tmp_dir.iterdir():
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _hash_file(path: Path) -> str:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(_HASH_CHUNK), b""):
                h.update(chunk)
        return h.hexdigest()

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
