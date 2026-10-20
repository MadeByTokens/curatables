"""Storage service — video file management, caching, and cleanup.

Downloads are delegated to a VideoBackend. This service handles file
paths, locking, subtitle listing, and cache management.
"""

from __future__ import annotations

import logging
import shutil
import threading
import time
import weakref
from dataclasses import dataclass, field
from pathlib import Path

from app.backends.base import VideoBackend
from app.models import Video
from app.services.disk_space import InsufficientDiskSpace, SpaceWatch
from app.services.ffmpeg_runner import CancelToken
from app.services.media_fs import DRIVE_MISSING, TOO_BIG_FOR_FAT, file_size_limit
from app.services.normalize import MediaNormalizer

logger = logging.getLogger(__name__)


RESOLUTION_MAP = {
    "360p": 360,
    "480p": 480,
    "720p": 720,
    "1080p": 1080,
}

_GB = 1_073_741_824

# Per-video locks so a download and an eviction never touch the same
# directory at once. Weak values let unused locks be collected.
_download_locks: "weakref.WeakValueDictionary[str, threading.Lock]" = weakref.WeakValueDictionary()
_locks_lock = threading.Lock()

_VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov", ".m4v")


@dataclass
class EvictionReport:
    """Summary of a cache-eviction sweep."""
    evicted_count: int = 0
    freed_bytes: int = 0
    video_ids: list[str] = field(default_factory=list)


def _entries(folder: Path) -> list[Path]:
    """What is in a video's folder, sorted; empty when it isn't there.
    A parent can delete a video while a kid's player is opening it, so
    the folder may vanish between any check and the listing."""
    try:
        return sorted(folder.iterdir())
    except (FileNotFoundError, NotADirectoryError):
        return []


def newest_mtime(path: Path) -> float:
    """The latest change under path. A folder's own mtime only moves when
    an entry is added or removed, not while a file in it is written."""
    newest = path.stat().st_mtime
    if path.is_dir():
        for sub in path.rglob("*"):
            try:
                newest = max(newest, sub.stat().st_mtime)
            except OSError:
                continue
    return newest


class StorageService:
    def __init__(self, media_dir: Path, backend: VideoBackend | None = None,
                 min_free_bytes: int = 0,
                 normalizer: MediaNormalizer | None = None,
                 max_download_bytes: int | None = None):
        self.media_dir = media_dir
        self.backend = backend
        self.min_free_bytes = min_free_bytes
        self.max_download_bytes = max_download_bytes
        # Brings each download to the playback baseline; None skips it.
        self.normalizer = normalizer

    def get_free_space(self) -> int:
        return shutil.disk_usage(self.media_dir).free

    def check_can_write(self, required_bytes: int = 0) -> tuple[bool, str]:
        """Return (allowed, reason). On refuse, reason names the numbers in GB."""
        if not self.media_dir.is_dir():
            return False, DRIVE_MISSING
        cap = file_size_limit(self.media_dir)
        if cap and required_bytes > cap:
            return False, TOO_BIG_FOR_FAT.format(size=required_bytes / _GB)
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
        return self.media_dir / "videos" / video_id

    def video_path(self, video_id: str) -> Path:
        return self.video_dir(video_id) / "video.mp4"

    def downloaded_file(self, video_id: str) -> Path | None:
        """The downloaded video file, whatever container it ended up in
        (normally video.mp4; a source the normalizer could not convert
        keeps its original .webm/.mkv)."""
        mp4 = self.video_path(video_id)
        if mp4.exists():
            return mp4
        for entry in _entries(self.video_dir(video_id)):
            if entry.stem == "video" and entry.suffix in _VIDEO_SUFFIXES:
                return entry
        return None

    def uploads_dir(self) -> Path:
        path = self.media_dir / "uploads"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def upload_tmp_dir(self) -> Path:
        path = self.uploads_dir() / ".tmp"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def sweep_upload_tmp(self, ttl_hours: int = 24) -> int:
        """Delete tmp upload files and finalize work folders older than
        ttl_hours. Import job folders (``import-*``) belong to the import
        worker, which sweeps its own. Returns count deleted."""
        tmp_dir = self.upload_tmp_dir()
        cutoff = time.time() - ttl_hours * 3600
        removed = 0
        for entry in tmp_dir.iterdir():
            try:
                if newest_mtime(entry) >= cutoff:
                    continue
                if entry.is_dir():
                    if entry.name.startswith("import-"):
                        continue
                    shutil.rmtree(entry)
                else:
                    entry.unlink()
                removed += 1
            except OSError:
                continue
        return removed

    def upload_video_dir(self, video_id: str) -> Path:
        return self.uploads_dir() / video_id

    def video_file_path(self, video: Video) -> Path:
        """Return the on-disk file for a Video: `uploads/{id}/video.<ext>`
        for uploads, see downloaded_file() for downloads."""
        if video.storage_mode == "uploaded":
            vdir = self.upload_video_dir(video.video_id)
            for entry in _entries(vdir):
                if entry.is_file() and entry.stem == "video":
                    return entry
            # Caller will get a non-existent path and handle the 404.
            return vdir / "video.missing"
        return self.downloaded_file(video.video_id) or self.video_path(video.video_id)

    def delete_video_files(self, video: Video) -> None:
        """Remove the on-disk directory backing a video.

        Works for both downloaded (`videos/<id>/`) and uploaded
        (`uploads/<id>/`) modes. Silent on missing directories and
        filesystem errors so a partial cleanup still lets the DB
        row be removed.
        """
        targets = [self.video_dir(video.video_id)]
        if video.storage_mode == "uploaded":
            targets.append(self.upload_video_dir(video.video_id))
        for target in targets:
            if target.exists():
                shutil.rmtree(target, ignore_errors=True)

    def is_cached(self, video_id: str) -> bool:
        return self.downloaded_file(video_id) is not None

    def file_size(self, video_id: str) -> int:
        path = self.downloaded_file(video_id)
        return path.stat().st_size if path else 0

    def subtitle_dir(self, video: Video) -> Path:
        """Where a video's subtitle files live: next to its video file."""
        if video.storage_mode == "uploaded":
            return self.upload_video_dir(video.video_id)
        return self.video_dir(video.video_id)

    def list_subtitles(self, video: Video) -> list[dict]:
        """Subtitle files for a video, named ``video.<lang>.vtt`` (or .srt)."""
        subs = []
        for f in _entries(self.subtitle_dir(video)):
            if f.suffix in (".vtt", ".srt") and f.is_file():
                parts = f.stem.split(".", 1)
                lang = parts[1] if len(parts) > 1 else ""
                subs.append({"lang": lang, "path": str(f), "filename": f.name})
        return subs

    def download(self, video_id: str, url: str,
                 resolution: str = "720p",
                 subtitle_langs: str = "all",
                 cancel: CancelToken | None = None) -> Path | None:
        """Download a video via the backend. Thread-safe with per-video locking.

        `video_id` is the composite storage key (used for filesystem
        path + per-video lock); `url` is the canonical source URL
        that actually gets handed to the backend. When `cancel` fires
        the download or conversion ends early (None, or Cancelled from
        the conversion) and its files are left for the caller.
        """
        lock = self._get_lock(video_id)

        if not lock.acquire(blocking=True, timeout=600):
            return None

        try:
            existing = self.downloaded_file(video_id)
            if existing is not None:
                # A power cut between download and conversion leaves the
                # original here; converting a file that is already
                # baseline only probes it.
                if self.normalizer is not None:
                    existing = self.normalizer.normalize(
                        existing, space=self.space_watch(), cancel=cancel).path
                return existing

            allowed, reason = self.check_can_write()
            if not allowed:
                raise InsufficientDiskSpace(reason)
            space = self.space_watch()

            height = RESOLUTION_MAP.get(resolution, 720)
            sub_list = None
            if subtitle_langs:
                if subtitle_langs == "all":
                    sub_list = ["all"]
                else:
                    sub_list = [s.strip() for s in subtitle_langs.split(",")]

            if self.backend is None:
                raise RuntimeError("StorageService has no download backend")
            path = self.backend.download_video(
                url,
                output_dir=self.video_dir(video_id),
                resolution=height,
                subtitle_langs=sub_list,
                max_filesize=self.max_download_bytes,
                keep_going=(space.ok if cancel is None
                            else lambda: space.ok() and not cancel.cancelled),
            )
            if cancel is not None and cancel.cancelled:
                return None
            if path is None and not space.ok():
                # Stopped (or failed) because the drive filled up: drop
                # the partial download, which can be gigabytes.
                self._remove_partial_download(video_id)
                raise InsufficientDiskSpace(space.message())
            if path and self.normalizer is not None:
                path = self.normalizer.normalize(path, space=space, cancel=cancel).path
            return path
        finally:
            lock.release()

    def space_watch(self) -> SpaceWatch:
        """A watch that trips when the videos drive falls under its minimum."""
        return SpaceWatch(self.media_dir, self.min_free_bytes)

    def remove_partial_download(self, video_id: str) -> None:
        """What a stopped download left: partial pieces and subtitles."""
        self._remove_partial_download(video_id)

    def _remove_partial_download(self, video_id: str) -> None:
        for f in _entries(self.video_dir(video_id)):
            if f.is_file() and (f.stem == "video" or f.name.startswith("video.")):
                f.unlink(missing_ok=True)

    def _get_lock(self, video_id: str) -> threading.Lock:
        with _locks_lock:
            lock = _download_locks.get(video_id)
            if lock is None:
                lock = threading.Lock()
                _download_locks[video_id] = lock
            return lock

    def _evict(self, video_repo, video_id: str, report: EvictionReport,
               keep_favourites: bool = True) -> None:
        """Delete one cached video's files and mark it evicted, re-checking
        eligibility under the per-video lock. keep_favourites=False is for
        the parent's own choice, which overrides keep_forever."""
        lock = self._get_lock(video_id)
        if not lock.acquire(blocking=True, timeout=5):
            logger.warning("evict: lock contention on %s, skipping", video_id)
            return
        try:
            video = video_repo.get(video_id)
            if (video is None or video.storage_mode != "cache"
                    or (video.keep_forever and keep_favourites)
                    or video.download_status != "ready"):
                return
            size_before = video.file_size or self.file_size(video_id)
            self.delete_video_files(video)
            video_repo.mark_evicted(video_id)
            report.evicted_count += 1
            report.freed_bytes += int(size_before)
            report.video_ids.append(video_id)
            logger.info("evicted cache video %s (%s) — freed %d bytes",
                        video_id, video.title, size_before)
        except Exception:
            logger.exception("evict failed for %s", video_id)
        finally:
            lock.release()

    def free_files(self, video_repo, video_ids: list[str]) -> EvictionReport:
        """Delete the files of the downloaded videos the parent picked; they
        stay listed and download again when a kid taps them. Uploads have
        no source to fetch again and are skipped."""
        report = EvictionReport()
        for video_id in video_ids:
            self._evict(video_repo, video_id, report, keep_favourites=False)
        return report

    def evict_expired(self, video_repo, cache_days: float) -> EvictionReport:
        """Delete cached videos nobody has watched for `cache_days`.

        Called by the background sweeper in app/main.py. A video's age is
        measured from when it was last played, or from when it was
        downloaded if it has never been played, so favourites watched
        every day are never evicted. No-op when `cache_days <= 0`.
        """
        report = EvictionReport()
        if cache_days <= 0:
            return report
        for video in video_repo.list_expired_cache(cache_days):
            self._evict(video_repo, video.video_id, report)
        return report
