from __future__ import annotations
"""yt-dlp implementation of the VideoBackend interface.

yt-dlp is the video extraction engine and supports ~1,800 sites. The
backend takes full URLs (not IDs) so it can pass them straight to
yt-dlp, and it captures the extractor key + webpage URL on every
successful fetch so the service layer can remember which platform a
video came from.
"""

import logging
import re
from pathlib import Path

from app.backends.base import VideoBackend, VideoMetadata, BackendError, BackendErrorType

logger = logging.getLogger(__name__)


# Only accept IDs yt-dlp gives us that are safe to use as filesystem
# path components. Anything else is a fixture-mode leak or a newly
# added extractor with weird IDs — skip it rather than crash.
_SAFE_ENTRY_ID = re.compile(r"^[a-zA-Z0-9._-]+$")


class YtdlpBackend(VideoBackend):
    """Video backend using yt-dlp as the extraction engine."""

    def __init__(self, cookies_from_browser: str = "",
                 cookies_file: str = "",
                 impersonate: str = ""):
        self.cookies_from_browser = cookies_from_browser
        self.cookies_file = cookies_file
        self.impersonate = impersonate
        self.last_error: BackendError | None = None

    def classify_error(self, error: Exception) -> BackendError:
        """Classify a yt-dlp exception into a structured error."""
        msg = str(error)
        details = msg

        if "Sign in to confirm" in msg or "bot" in msg.lower():
            return BackendError(
                error_type=BackendErrorType.BOT_BLOCKED,
                message="The source blocked the request (bot detection).",
                details=details,
                suggestion="Go to Settings and enable Browser Impersonation, "
                           "or set up a cookie file.",
            )
        if "age" in msg.lower() and ("restrict" in msg.lower() or "verif" in msg.lower()):
            return BackendError(
                error_type=BackendErrorType.AGE_RESTRICTED,
                message="This video requires age verification.",
                details=details,
                suggestion="Set up source authentication in Settings "
                           "(cookie file or browser cookies from a logged-in account).",
            )
        if "unavailable" in msg.lower() or "private" in msg.lower() or "removed" in msg.lower():
            return BackendError(
                error_type=BackendErrorType.VIDEO_UNAVAILABLE,
                message="This video is unavailable (private, deleted, or region-locked).",
                details=details,
            )
        if "connect" in msg.lower() or "timeout" in msg.lower() or "dns" in msg.lower() or "network" in msg.lower():
            return BackendError(
                error_type=BackendErrorType.NETWORK,
                message="Network error — could not reach the source.",
                details=details,
                suggestion="Check your internet connection and try again.",
            )
        if "no video formats" in msg.lower() or "requested format" in msg.lower():
            return BackendError(
                error_type=BackendErrorType.FORMAT,
                message="No suitable video format found.",
                details=details,
                suggestion="Try a different resolution in Settings.",
            )
        return BackendError(
            error_type=BackendErrorType.UNKNOWN,
            message="An unexpected error occurred.",
            details=details,
            suggestion="Check the server logs for details.",
        )

    def _base_opts(self) -> dict:
        """Common yt-dlp options shared across all operations.

        Anti-bot strategy priority:
        1. Browser impersonation (best — no login needed, works headless)
        2. Cookie file (for cases where impersonation isn't enough)
        3. Browser cookies (desktop only — reads from local browser)
        """
        opts = {"quiet": True, "no_warnings": True}
        # Impersonation — makes yt-dlp's TLS look like a real browser
        if self.impersonate:
            try:
                from yt_dlp.networking.impersonate import ImpersonateTarget
                opts["impersonate"] = ImpersonateTarget.from_str(self.impersonate)
            except (ImportError, Exception):
                pass  # curl_cffi not available, skip silently
        # Cookie auth — fallback or supplement
        if self.cookies_file:
            opts["cookiefile"] = self.cookies_file
        elif self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        # YouTube-specific: try every player client. yt-dlp silently
        # ignores per-extractor args for non-matching extractors, so
        # leaving this on for all requests is harmless.
        opts["extractor_args"] = {"youtube": {"player_client": ["all"]}}
        return opts

    def fetch_video_info(self, url: str) -> VideoMetadata | None:
        import yt_dlp

        opts = {**self._base_opts(), "skip_download": True}

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(url, download=False)
            except Exception as e:
                self.last_error = self.classify_error(e)
                logger.warning("[%s] %s", self.last_error.error_type.value, self.last_error.message)
                logger.debug("  Details: %s", self.last_error.details)
                return None

        if not info:
            return None

        return _info_to_metadata(info)

    def fetch_channel_videos(self, channel_url: str,
                             max_results: int = 50) -> tuple[str, list[VideoMetadata]]:
        import yt_dlp

        # Legacy convenience: callers used to be able to pass a bare
        # "@handle" for a YouTube channel. Keep the convenience, but
        # anything that already looks like a URL is passed through
        # untouched so non-YouTube channel URLs aren't clobbered.
        if not channel_url.startswith("http"):
            channel_url = f"https://www.youtube.com/{channel_url}"
        if "youtube.com" in channel_url and "/videos" not in channel_url:
            # Only the YouTube channel-URL convention needs /videos
            # appended; other platforms route differently.
            channel_url = channel_url.rstrip("/") + "/videos"

        opts = {
            **self._base_opts(),
            "extract_flat": True, "skip_download": True,
            "playlistend": max_results,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(channel_url, download=False)
            except Exception as e:
                self.last_error = self.classify_error(e)
                logger.warning("[%s] %s", self.last_error.error_type.value, self.last_error.message)
                logger.debug("  Details: %s", self.last_error.details)
                return "Unknown", []

        title = (info.get("channel") or info.get("uploader")
                 or info.get("title") or "Unknown")
        extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
        return title, _entries_to_metadata(info.get("entries", []), title,
                                           parent_extractor=extractor)

    def fetch_playlist(self, playlist_url: str,
                       max_results: int = 50) -> tuple[str, list[VideoMetadata]]:
        import yt_dlp

        opts = {
            **self._base_opts(),
            "extract_flat": True, "skip_download": True,
            "playlistend": max_results,
        }

        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                info = ydl.extract_info(playlist_url, download=False)
            except Exception as e:
                self.last_error = self.classify_error(e)
                logger.warning("[%s] %s", self.last_error.error_type.value, self.last_error.message)
                logger.debug("  Details: %s", self.last_error.details)
                return "Unknown", []

        title = info.get("title") or "Unknown Playlist"
        extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
        return title, _entries_to_metadata(info.get("entries", []),
                                           parent_extractor=extractor)

    def download_video(self, url: str, output_dir: Path,
                       resolution: int = 720,
                       subtitle_langs: list[str] | None = None) -> Path | None:
        import yt_dlp

        output_dir.mkdir(parents=True, exist_ok=True)

        # Prefer H.264 video (avc1) + AAC audio (mp4a) so the result is in
        # the client playback baseline straight from the source and needs
        # no transcode. Modern YouTube serves VP9/AV1+Opus; those fall
        # through to the last clauses and the ingest-time normalizer
        # (app/services/normalize.py) transcodes them to H.264/AAC.
        fmt = (
            f"bestvideo[vcodec^=avc1][height<={resolution}]+bestaudio[acodec^=mp4a]/"
            f"bestvideo[vcodec^=avc1][height<={resolution}]+bestaudio/"
            f"best[vcodec^=avc1][height<={resolution}]/"
            f"best[height<={resolution}]/best"
        )
        opts = {
            **self._base_opts(),
            "format": fmt,
            "merge_output_format": "mp4",
            "outtmpl": str(output_dir / "video.%(ext)s"),
        }

        if subtitle_langs:
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitlesformat"] = "vtt"
            opts["subtitleslangs"] = subtitle_langs

        try:
            with yt_dlp.YoutubeDL(opts) as ydl:
                ydl.download([url])
        except Exception as e:
            self.last_error = self.classify_error(e)
            logger.exception("Download failed for %s", url)
            return None

        # yt-dlp usually produces video.mp4 (merge_output_format), but a
        # single-format pull or a failed merge can leave video.webm /
        # video.mkv. Return whatever real file landed — do NOT rename a
        # .webm/.mkv to .mp4 (that lies about the codecs inside). The
        # ingest-time normalizer transcodes non-mp4 / non-baseline files
        # into a true baseline video.mp4.
        expected = output_dir / "video.mp4"
        if expected.exists():
            return expected
        for f in sorted(output_dir.iterdir()):
            if f.stem == "video" and f.suffix in (".webm", ".mkv", ".mov", ".m4v"):
                return f
        return None


# --- Helpers (private to this module) ---

def _parse_duration(value) -> int:
    if not value:
        return 0
    if isinstance(value, str):
        try:
            return int(float(value))
        except (ValueError, TypeError):
            return 0
    return int(value)


def _info_to_metadata(info: dict) -> VideoMetadata:
    """Project a yt-dlp info dict onto our VideoMetadata shape.

    Preserves `extractor_key` and `webpage_url` so the service layer
    can route per-platform decisions (embed URL building, the
    "original" link) without re-running yt-dlp.
    """
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    return VideoMetadata(
        video_id=info.get("id", ""),
        title=info.get("title", "Unknown"),
        channel=info.get("channel") or info.get("uploader") or "Unknown",
        duration=_parse_duration(info.get("duration")),
        view_count=info.get("view_count") or 0,
        upload_date=info.get("upload_date") or "",
        description=info.get("description") or "",
        thumbnail_url=info.get("thumbnail") or "",
        extractor=extractor,
        original_url=info.get("webpage_url") or info.get("url") or "",
    )


def _entries_to_metadata(entries: list,
                         default_channel: str = "Unknown",
                         parent_extractor: str = "") -> list[VideoMetadata]:
    """Turn a yt-dlp entries list into VideoMetadata objects.

    `parent_extractor` is the `extractor_key` of the containing
    channel/playlist — used as a fallback when individual entries
    come back without their own extractor field (which happens in
    flat-extract mode). Entries with IDs that aren't safe to use as
    filesystem path components are skipped (prevents path traversal
    if an extractor ever returns a weird ID).
    """
    results = []
    for e in entries:
        if not e:
            continue
        vid_id = e.get("id", "")
        if not vid_id or not _SAFE_ENTRY_ID.match(vid_id):
            continue
        extractor = (e.get("extractor_key") or e.get("extractor")
                     or parent_extractor or "").lower()
        results.append(VideoMetadata(
            video_id=vid_id,
            title=e.get("title", "N/A"),
            channel=e.get("channel") or e.get("uploader") or default_channel,
            duration=_parse_duration(e.get("duration")),
            view_count=e.get("view_count") or 0,
            upload_date=e.get("upload_date") or "",
            description=e.get("description") or "",
            thumbnail_url=e.get("thumbnail") or "",
            extractor=extractor,
            original_url=e.get("webpage_url") or e.get("url") or "",
        ))
    return results
