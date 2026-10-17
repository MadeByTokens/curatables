"""Turn a local video file into a library video.

Shared by parent uploads and drive imports. Every step before commit()
works inside one work folder under uploads/.tmp/, so a failure or a
cancel is cleaned up by deleting that folder. commit() is short: it
moves the finished files into uploads/<id>/ and inserts the row.
"""

from __future__ import annotations

import hashlib
import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from app.models import Video
from app.repositories import VideoRepository
from app.services.ffmpeg_runner import CancelToken, Progress, ProgressCallback
from app.services.media_probe import MediaProbeService, ProbeError, ProbeResult
from app.services.normalize import NormalizeResult
from app.services.storage import StorageService
from app.services.thumbnails import ThumbnailService

logger = logging.getLogger(__name__)

HASH_CHUNK = 8 * 1024 * 1024


@dataclass
class VideoDetails:
    """What the parent chose for a new video."""
    title: str
    channel_id: int | None
    status: str = "active"
    description: str = ""
    channel_name: str = ""
    original_url: str = ""
    upload_date: str = ""
    thumbnail_bytes: bytes | None = None
    extra: dict = field(default_factory=dict)
    """More videos columns to set after insert (e.g. origin, origin_ref)."""


def upload_video_id(digest: str) -> str:
    return f"up_{digest[:16]}"


class IngestService:
    def __init__(self, storage: StorageService, probe: MediaProbeService,
                 video_repo: VideoRepository, thumbnails: ThumbnailService):
        self.storage = storage
        self.probe = probe
        self.video_repo = video_repo
        self.thumbnails = thumbnails

    def work_dir(self, name: str) -> Path:
        path = self.storage.upload_tmp_dir() / name
        path.mkdir(parents=True, exist_ok=True)
        return path

    @staticmethod
    def hash_file(path: Path, on_progress: ProgressCallback | None = None,
                  cancel: CancelToken | None = None) -> str:
        total = path.stat().st_size
        done = 0
        h = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(HASH_CHUNK):
                if cancel is not None:
                    cancel.check()
                h.update(chunk)
                done += len(chunk)
                if on_progress is not None:
                    on_progress(Progress(phase="hash", done=done, total=total))
        return h.hexdigest()

    def normalize(self, path: Path, probe: ProbeResult, *,
                  on_progress: ProgressCallback | None = None,
                  cancel: CancelToken | None = None, audio_index: int | None = None,
                  burn_subtitle_index: int | None = None
                  ) -> tuple[Path, ProbeResult, NormalizeResult | None]:
        """Bring the file to the playback baseline in place (inside the
        work folder). Returns the final path and a refreshed probe."""
        normalizer = self.storage.normalizer
        if normalizer is None:
            return path, probe, None
        norm = normalizer.normalize(path, on_progress=on_progress, cancel=cancel,
                                    audio_index=audio_index,
                                    burn_subtitle_index=burn_subtitle_index,
                                    space=self.storage.space_watch())
        if norm.action in ("transcode", "remux"):
            try:
                probe = self.probe.probe(norm.path)
            except ProbeError:
                pass
        return norm.path, probe, norm

    def commit(self, work: Path, video_path: Path, video_id: str, probe: ProbeResult,
               details: VideoDetails) -> Video:
        """Move the finished video (and any ``video.<lang>.vtt`` next to it)
        into uploads/<id>/, set the picture and insert the row."""
        target_dir = self.storage.upload_video_dir(video_id)
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / f"video{video_path.suffix.lower()}"
        video_path.replace(target_path)
        for sub in work.glob("video.*.vtt"):
            sub.replace(target_dir / sub.name)

        try:
            if details.thumbnail_bytes:
                self.thumbnails.save_uploaded(video_id, details.thumbnail_bytes)
            else:
                self.thumbnails.extract_frame(video_id, source_path=target_path)
        except Exception as e:
            logger.warning("Thumbnail for %s failed: %s", video_id, e)

        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        size = target_path.stat().st_size
        video = Video(
            video_id=video_id,
            title=details.title,
            original_title=details.title,
            channel_name=details.channel_name,
            description=details.description,
            duration=int(probe.duration_seconds or 0),
            upload_date=details.upload_date or now[:10],
            view_count=0,
            thumbnail_url="",
            thumbnail_type="custom" if details.thumbnail_bytes else "frame",
            status=details.status,
            download_status="ready",
            storage_mode="uploaded",
            resolution=f"{probe.height}p" if probe.height else "",
            source_id=None,
            channel_id=details.channel_id,
            file_size=size,
            original_url=details.original_url,
        )
        self.video_repo.insert(video)
        self.video_repo.update(video_id, cached_at=now, file_size=size, **details.extra)
        shutil.rmtree(work, ignore_errors=True)
        return self.video_repo.get(video_id) or video
