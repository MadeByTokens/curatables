"""Pause and stop for downloads that are running.

The parent's request is a flag per video, checked by yt-dlp's progress
hook and by the conversion's cancel token. The job then ends the way
the flag says: paused keeps the partial file, so the download carries
on from there; stopped removes it.
"""

from __future__ import annotations

import threading

from app.services.ffmpeg_runner import CancelToken, Cancelled

PAUSE = "pause"
STOP = "stop"

_requests: dict[str, str] = {}
_lock = threading.Lock()


def request(video_id: str, action: str) -> None:
    """Ask a running download to end. Stop wins over pause."""
    with _lock:
        if _requests.get(video_id) != STOP:
            _requests[video_id] = action


def wanted(video_id: str) -> str | None:
    with _lock:
        return _requests.get(video_id)


def take(video_id: str) -> str | None:
    """The request for this video, cleared."""
    with _lock:
        return _requests.pop(video_id, None)


def clear(video_id: str) -> None:
    with _lock:
        _requests.pop(video_id, None)


class RequestToken(CancelToken):
    """A cancel token that fires once the parent pauses or stops this
    video, for the conversion step."""

    def __init__(self, video_id: str) -> None:
        super().__init__()
        self.video_id = video_id

    @property
    def cancelled(self) -> bool:
        return wanted(self.video_id) is not None

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()
