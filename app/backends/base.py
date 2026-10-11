"""Abstract interface for video backends.

The only implementation is yt-dlp (ytdlp.py); another tool can be
plugged in by implementing VideoBackend and returning it from
app/dependencies.py.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


@dataclass
class VideoMetadata:
    """Metadata for a single video, as returned by the backend.

    `video_id` is the raw platform ID (11 characters on YouTube); the
    composite `{extractor}_{raw}` ID is built by
    `app.services.ids.make_video_id`.
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
    is_live: bool = False     # live now or not started yet


class BackendErrorType(Enum):
    """Classified error types for user-facing messages and debugging."""
    BOT_BLOCKED = "bot_blocked"         # source blocked the request as a bot
    RATE_LIMITED = "rate_limited"       # source asked us to slow down (HTTP 429)
    VIDEO_UNAVAILABLE = "unavailable"   # video is private, deleted, region-locked
    NETWORK = "network"                 # connection timeout, DNS failure, etc.
    AGE_RESTRICTED = "age_restricted"   # requires age verification
    FORMAT = "format"                   # no suitable format found
    BLOCKED = "blocked"                 # address on the home network; never fetched
    UNKNOWN = "unknown"                 # unclassified error


@dataclass
class BackendError:
    """Structured error from a backend operation."""
    error_type: BackendErrorType
    message: str           # human-readable message for the parent
    details: str           # full technical details for debugging / developer logs
    suggestion: str = ""   # actionable suggestion for the user


@dataclass
class ChannelHit:
    """A channel in search results."""
    channel_id: str
    name: str
    thumbnail_url: str = ""
    followers: int = 0


class VideoBackend(ABC):
    """Interface that any video source backend must implement.

    The fetch and download methods take a full URL, not an ID, so the
    service layer does not need to know which platform it belongs to.
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

    def search(self, query: str, max_results: int = 12,
               newest_first: bool = False) -> list[VideoMetadata]:
        """YouTube search results for `query` (metadata only)."""
        raise NotImplementedError

    def search_channels(self, query: str, max_results: int = 12) -> list[ChannelHit]:
        """YouTube channels matching `query`."""
        raise NotImplementedError

    @abstractmethod
    def download_video(self, url: str, output_dir: Path,
                       resolution: int = 720,
                       subtitle_langs: list[str] | None = None,
                       max_filesize: int | None = None) -> Path | None:
        """Download a video from its full URL to output_dir.
        Returns the path to the downloaded file, or None on failure
        (including a source file larger than `max_filesize` bytes)."""
        ...
