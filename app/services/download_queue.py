"""Process-wide download queue.

Every download (new video, startup resume, re-download after eviction)
runs on one small worker pool so a 50-video playlist on a Raspberry Pi
becomes 50 queued jobs instead of 50 concurrent yt-dlp + ffmpeg runs.
Rows waiting in the queue stay `download_status='pending'`; the job
flips them to `downloading` when it actually starts.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

logger = logging.getLogger(__name__)


class DownloadQueue:
    def __init__(self, max_workers: int = 1):
        self.max_workers = max(1, int(max_workers))
        self._executor = ThreadPoolExecutor(
            max_workers=self.max_workers, thread_name_prefix="download")
        self._active: dict[str, Future] = {}
        self._lock = threading.Lock()

    def submit(self, video_id: str, job: Callable[[], None]) -> bool:
        """Queue `job` for `video_id`. Returns False if that video is
        already queued or running."""
        with self._lock:
            if video_id in self._active:
                return False
            future = self._executor.submit(self._run, video_id, job)
            self._active[video_id] = future
            return True

    def _run(self, video_id: str, job: Callable[[], None]) -> None:
        try:
            job()
        except Exception:
            logger.exception("Download job for %s crashed", video_id)
        finally:
            with self._lock:
                self._active.pop(video_id, None)

    def pending_count(self) -> int:
        with self._lock:
            return len(self._active)

    def wait_idle(self, timeout: float | None = None) -> None:
        """Block until every queued job has finished (used by tests)."""
        with self._lock:
            futures = list(self._active.values())
        for f in futures:
            f.result(timeout=timeout)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)


_queue: DownloadQueue | None = None
_queue_lock = threading.Lock()


def get_download_queue(max_workers: int | None = None) -> DownloadQueue:
    """Return the shared queue, creating it on first use. Passing a
    different `max_workers` replaces an idle queue with a resized one."""
    global _queue
    with _queue_lock:
        if _queue is None:
            _queue = DownloadQueue(max_workers or 1)
        elif (max_workers is not None and max_workers != _queue.max_workers
              and _queue.pending_count() == 0):
            _queue.shutdown()
            _queue = DownloadQueue(max_workers)
        return _queue
