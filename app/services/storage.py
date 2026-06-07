from __future__ import annotations
"""Storage service — video file management, caching, and cleanup.

Downloads are delegated to a VideoBackend. This service handles file
paths, locking, subtitle listing, and cache management.
"""

import logging
import shutil
import threading
from dataclasses import dataclass, field
from pathlib import Path

from app.backends.base import VideoBackend
from app.services.normalize import MediaNormalizer

logger = logging.getLogger(__name__)


RESOLUTION_MAP = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
}

_GB = 1_073_741_824

# Per-video download locks to prevent duplicate concurrent downloads
_download_locks: dict[str, threading.Lock] = {}
_locks_lock = threading.Lock()


class InsufficientDiskSpace(Exception):
    """Raised when a write would drop free space below the configured minimum."""


@dataclass
class EvictionReport:
    """Summary of a cache-eviction sweep."""
    evicted_count: int = 0
    freed_bytes: int = 0
    video_ids: list[str] = field(default_factory=list)


class StorageService:
    def __init__(self, data_dir: Path, backend: VideoBackend, min_free_bytes: int = 0,
                 normalizer: MediaNormalizer | None = None):
        self.data_dir = data_dir
        self.backend = backend
        self.min_free_bytes = min_free_bytes
        # When set, every freshly downloaded file is brought to the
        # client playback baseline (H.264/AAC ≤720p30 +faststart) before
        # download() returns. Optional so unit tests can construct a bare
        # StorageService without ffmpeg wiring.
        self.normalizer = normalizer

    def get_free_space(self) -> int:
        return shutil.disk_usage(self.data_dir).free

    def check_can_write(self, required_bytes: int = 0) -> tuple[bool, str]:
        """Return (allowed, reason). On refuse, reason names the numbers in GB."""
        free = self.get_free_space()
        projected_free = free - required_bytes
        if projected_free >= self.min_free_bytes:
            return True, ""
        free_gb = free / _GB
        need_gb = self.min_free_bytes / _GB
        if required_bytes > 0:
            req_gb = required_bytes / _GB
            reason = (f"Insufficient disk space: {free_gb:.1f} GB free, "
                      f"need {req_gb:.1f} GB and a minimum of {need_gb:.1f} GB "
                      f"must remain. Free up space or lower the threshold in Settings.")
        else:
            reason = (f"Insufficient disk space: {free_gb:.1f} GB free, "
                      f"minimum {need_gb:.1f} GB required. "
                      f"Free up space or lower the threshold in Settings.")
        return False, reason

    def video_dir(self, video_id: str) -> Path:
        return self.data_dir / "videos" / video_id

    def video_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "video.mp4"

    def uploads_dir(self) -> Path:
        path = self.data_dir / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def upload_tmp_dir(self) -> Path:
        path = self.uploads_dir() / ".tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def upload_video_dir(self, video_id: str) -> Path:
        return self.uploads_dir() / video_id

    def video_file_path(self, video) -> Path:
        """Return the on-disk file for a Video, branching on storage_mode.

        Uploaded videos are stored at `uploads/{id}/video.<ext>` where
        the extension depends on the original container. We glob
        `video.*` and return the first match. yt-dlp downloads live
        at `videos/{id}/video.mp4` (always .mp4 since the backend
        requests that container explicitly).
        """
        if getattr(video, "storage_mode", "") == "uploaded":
            vdir = self.upload_video_dir(video.video_id)
            if vdir.exists():
                for entry in vdir.iterdir():
                    if entry.is_file() and entry.stem == "video":
                        return entry
            # Caller will get a non-existent path and handle the 404.
            return vdir / "video.missing"
        return self.video_path(video.video_id)

    def delete_video_files(self, video) -> None:
        """Remove the on-disk directory backing a video.

        Works for both downloaded (`videos/<id>/`) and uploaded
        (`uploads/<id>/`) modes. Silent on missing directories and
        filesystem errors so a partial cleanup still lets the DB
        row be removed.
        """
        if getattr(video, "storage_mode", "") == "uploaded":
            target = self.upload_video_dir(video.video_id)
        else:
            target = self.video_dir(video.video_id)
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)

    def is_cached(self, video_id: str) -> bool:
        return self.video_path(video_id).exists()

    def file_size(self, video_id: str) -> int:
        path = self.video_path(video_id)
        return path.stat().st_size if path.exists() else 0

    def list_subtitles(self, video_id: str) -> list[dict]:
        """List available subtitle files for a video."""
        vdir = self.video_dir(video_id)
        subs = []
        if vdir.exists():
            for f in vdir.iterdir():
                if f.suffix in (".vtt", ".srt"):
                    parts = f.stem.split(".", 1)
                    lang = parts[1] if len(parts) > 1 else "unknown"
                    subs.append({"lang": lang, "path": str(f), "filename": f.name})
        return subs

    def download(self, video_id: str, url: str,
                 resolution: str = "720p",
                 subtitle_langs: str = "all") -> Path | None:
        """Download a video via the backend. Thread-safe with per-video locking.

        `video_id` is the composite storage key (used for filesystem
        path + per-video lock); `url` is the canonical source URL
        that actually gets handed to the backend.
        """
        lock = self._get_lock(video_id)

        if not lock.acquire(blocking=True, timeout=600):
            return None

        try:
            if self.is_cached(video_id):
                return self.video_path(video_id)

            allowed, reason = self.check_can_write()
            if not allowed:
                raise InsufficientDiskSpace(reason)

            height = RESOLUTION_MAP.get(resolution, 720)
            sub_list = None
            if subtitle_langs:
                if subtitle_langs == "all":
                    sub_list = ["all"]
                else:
                    sub_list = [s.strip() for s in subtitle_langs.split(",")]

            path = self.backend.download_video(
                url,
                output_dir=self.video_dir(video_id),
                resolution=height,
                subtitle_langs=sub_list,
            )
            if path and self.normalizer is not None:
                # Bring the pulled stream into the client playback
                # baseline. normalize() never raises on media problems —
                # a file it can't read/transcode is left untouched.
                path = self.normalizer.normalize(path).path
            return path
        finally:
            lock.release()

    def _get_lock(self, video_id: str) -> threading.Lock:
        with _locks_lock:
            if video_id not in _download_locks:
                _download_locks[video_id] = threading.Lock()
            return _download_locks[video_id]

    def evict_expired(self, video_repo, cache_days: float) -> EvictionReport:
        """Delete cached video files whose cache has expired.

        Called by the hourly background sweeper in app/main.py. The
        eligibility query lives in the repository (`list_expired_cache`);
        this method owns the filesystem + state-transition half.

        Returns an EvictionReport; caller is responsible for logging
        the summary. Individual evictions are logged at INFO here.

        No-op when `cache_days <= 0` (cache-forever mode).
        """
        report = EvictionReport()
        if cache_days <= 0:
            return report

        expired = video_repo.list_expired_cache(cache_days)
        for video in expired:
            # Cooperate with the downloader lock: if a download is
            # in flight for this video_id (shouldn't be, since the
            # query filters on download_status='ready', but defend
            # against races), wait briefly then give up.
            lock = self._get_lock(video.video_id)
            if not lock.acquire(blocking=True, timeout=5):
                logger.warning("evict_expired: lock contention on %s, skipping",
                               video.video_id)
                continue
            try:
                size_before = video.file_size or self.file_size(video.video_id)
                self.delete_video_files(video)
                video_repo.mark_evicted(video.video_id)
                report.evicted_count += 1
                report.freed_bytes += int(size_before)
                report.video_ids.append(video.video_id)
                logger.info(
                    "evicted cache video %s (%s) — freed %d bytes",
                    video.video_id, video.title, size_before,
                )
            except Exception:
                # Don't let one rotten row stop the sweep.
                logger.exception("evict_expired failed for %s", video.video_id)
            finally:
                lock.release()

        return report
