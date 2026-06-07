from __future__ import annotations
"""Video source service — platform-agnostic URL parsing + fetching.

Wraps a VideoBackend (currently only yt-dlp) and adds a lightweight
URL classifier so the route layer can tell the difference between a
single-video URL, a channel URL, and a playlist URL *without* a
network round-trip for the common cases.

URL classification has two layers:

1. **Fast path** — known regexes for YouTube, recognised without
   touching the network. Returns the canonicalised clean URL.
2. **Catch-all** — any other `http(s)://` URL is handed back as a
   generic "video" ParsedURL; the backend (yt-dlp) decides the
   actual type when it calls `extract_info`. This is how support
   for the ~1,800 non-YouTube sites yt-dlp knows about drops in
   for free.

The only thing we reject is input that isn't a URL at all — no
string starting with `http://` or `https://`, and not a bare YouTube
`@handle` (which we still accept as a convenience). Everything else
gets a chance at the backend.
"""

import re
from dataclasses import dataclass

from app.backends.base import VideoBackend, VideoMetadata


@dataclass
class ParsedURL:
    url_type: str      # "video" | "channel" | "playlist"
    resource_id: str   # raw ID (YouTube video ID, playlist ID, channel handle)
                       # or the full URL for the catch-all path
    clean_url: str     # canonicalised URL to hand to the backend


class VideoSourceService:
    """Thin wrapper around a VideoBackend that adds URL parsing."""

    def __init__(self, backend: VideoBackend):
        self.backend = backend

    def parse_url(self, url: str) -> ParsedURL | None:
        """Detect URL type and extract an ID (YouTube fast path), or
        return a catch-all ParsedURL for any other http(s) URL.

        Returns None only for input that isn't plausibly a URL at all.
        """
        url = (url or "").strip()
        if not url:
            return None

        # --- Fast path: YouTube URL shapes ---

        m = re.search(r'(?:watch\?v=|youtu\.be/|/shorts/)([a-zA-Z0-9_-]{11})', url)
        if m:
            vid = m.group(1)
            return ParsedURL(
                url_type="video",
                resource_id=vid,
                clean_url=f"https://www.youtube.com/watch?v={vid}",
            )

        m = re.search(r'list=([a-zA-Z0-9_-]+)', url)
        if m:
            return ParsedURL(
                url_type="playlist",
                resource_id=m.group(1),
                clean_url=url,
            )

        m = re.search(
            r'youtube\.com/([@][\w.-]+|channel/[\w-]+|c/[\w.-]+|user/[\w.-]+)',
            url,
        )
        if m:
            return ParsedURL(
                url_type="channel",
                resource_id=m.group(1),
                clean_url=url,
            )

        # Bare @handle shorthand for a YouTube channel
        if url.startswith("@") and re.fullmatch(r"@[\w.-]+", url):
            return ParsedURL(
                url_type="channel",
                resource_id=url,
                clean_url=f"https://www.youtube.com/{url}",
            )

        # --- Catch-all: any other http(s) URL ---
        #
        # The backend's extract_info call will tell us the real type
        # (single video vs. playlist vs. channel). For now we label
        # it "video" because that's the dominant case; the
        # ContentService.fetch_preview path then decides whether to
        # call fetch_video_info / fetch_playlist / fetch_channel_videos
        # based on the url_type here. A non-video URL (e.g. a
        # Vimeo showcase) will fall through to fetch_video_info,
        # which yt-dlp still handles gracefully — it returns
        # playlist entries in a `_type: "playlist"` info dict and
        # the service layer can decide to promote it.
        if url.startswith("http://") or url.startswith("https://"):
            return ParsedURL(
                url_type="video",
                resource_id=url,
                clean_url=url,
            )

        return None

    # --- Passthroughs to the backend. All take URLs, not IDs. ---

    def fetch_video_info(self, url: str) -> VideoMetadata | None:
        return self.backend.fetch_video_info(url)

    def fetch_channel_videos(self, channel_url: str,
                             max_results: int = 50) -> tuple[str, list[VideoMetadata]]:
        return self.backend.fetch_channel_videos(channel_url, max_results)

    def fetch_playlist(self, url: str,
                       max_results: int = 50) -> tuple[str, list[VideoMetadata]]:
        return self.backend.fetch_playlist(url, max_results)
