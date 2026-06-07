from __future__ import annotations
"""Abstract interface for video backends.

This defines what curatables needs from a video source. The only
implementation today is yt-dlp (in ytdlp.py), but this interface
allows swapping to a fork, a custom scraper, or a different tool
without changing any service or route code.

To add a new backend:
1. Create a new file in app/backends/ (e.g. mybackend.py)
2. Implement VideoBackend with the same methods
3. Update app/dependencies.py to return your implementation
"""

from abc import ABC, abstractmethod
from enum import Enum
from dataclasses import dataclass
from pathlib import Path


@dataclass
class VideoMetadata:
    """Metadata for a single video, as returned by the backend.

    `video_id` here is the raw, extractor-scoped ID that yt-dlp
    returns (11 chars for YouTube, numeric for Vimeo, etc.). The
    composite `{extractor}_{raw}` form is assembled higher up, by
    the service/route layer via `app.services.ids.make_video_id`.
    """
    video_id: str
    title: str
    channel: str
    duration: int
    view_count: int
    upload_date: str
    description: str
    thumbnail_url: str
    extractor: str = ""       # from info["extractor_key"], lowercased
    original_url: str = ""    # from info["webpage_url"]


class BackendErrorType(Enum):
    """Classified error types for user-facing messages and debugging."""
    BOT_BLOCKED = "bot_blocked"         # source blocked the request as a bot
    VIDEO_UNAVAILABLE = "unavailable"   # video is private, deleted, region-locked
    NETWORK = "network"                 # connection timeout, DNS failure, etc.
    AGE_RESTRICTED = "age_restricted"   # requires age verification
    FORMAT = "format"                   # no suitable format found
    UNKNOWN = "unknown"                 # unclassified error


@dataclass
class BackendError:
    """Structured error from a backend operation."""
    error_type: BackendErrorType
    message: str           # human-readable message for the parent
    details: str           # full technical details for debugging / developer logs
    suggestion: str = ""   # actionable suggestion for the user


class VideoBackend(ABC):
    """Interface that any video source backend must implement.

    All four fetch/download methods take a *full URL* (not an ID).
    The backend is responsible for passing it to whatever underlying
    tool it wraps; the service layer has already resolved the URL
    via `VideoSourceService.parse_url` but otherwise doesn't know or
    care which platform the URL belongs to.
    """

    last_error: BackendError | None = None

    @abstractmethod
    def fetch_video_info(self, url: str) -> VideoMetadata | None:
        """Fetch metadata for a single video from its full URL."""
        ...

    @abstractmethod
    def fetch_channel_videos(self, channel_url: str,
                             max_results: int = 50) -> tuple[str, list[VideoMetadata]]:
        """Fetch recent videos from a channel URL.
        Returns (channel_title, list_of_videos)."""
        ...

    @abstractmethod
    def fetch_playlist(self, playlist_url: str,
                       max_results: int = 50) -> tuple[str, list[VideoMetadata]]:
        """Fetch videos from a playlist URL.
        Returns (playlist_title, list_of_videos)."""
        ...

    @abstractmethod
    def download_video(self, url: str, output_dir: Path,
                       resolution: int = 720,
                       subtitle_langs: list[str] | None = None) -> Path | None:
        """Download a video from its full URL to output_dir.
        Returns the path to the downloaded file, or None on failure."""
        ...
