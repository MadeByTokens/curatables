"""yt-dlp implementation of the VideoBackend interface.

Every fetch records the extractor key and webpage URL, so the service
layer knows which platform a video came from.
"""

from __future__ import annotations

import logging
import os
import re
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote_plus, urlsplit

from app.backends.base import BackendError, BackendErrorType, ChannelHit, VideoBackend, VideoMetadata
from app.services.safe_fetch import BlockedURL, check_url

if TYPE_CHECKING:
    from app.config import Config

logger = logging.getLogger(__name__)


# Entry IDs become filesystem path components; anything else is skipped.
_SAFE_ENTRY_ID = re.compile(r"^[a-zA-Z0-9._-]+$")
_YOUTUBE_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
# Titles flat playlist listings give videos that can no longer be watched.
_GONE_TITLES = {"[Private video]", "[Deleted video]"}
_SUBTITLE_FAILED = "Unable to download video subtitles"
_SUBTITLE_OPTS = ("writesubtitles", "writeautomaticsub", "subtitlesformat", "subtitleslangs")


class _StopDownload(Exception):
    """Raised from a progress hook to stop a download part way."""
_YOUTUBE_CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")


class YtdlpBackend(VideoBackend):
    """Video backend using yt-dlp as the extraction engine."""

    def __init__(self, cookies_from_browser: str = "",
                 cookies_file: str = "",
                 impersonate: str = "",
                 cache_dir: Path | None = None):
        self.cookies_from_browser = cookies_from_browser
        self.cookies_file = cookies_file
        self.impersonate = impersonate
        self.cache_dir = cache_dir
        self._local = threading.local()
        self._cookies_copied_from: tuple[int, int] | None = None

    @classmethod
    def from_config(cls, config: Config) -> YtdlpBackend:
        # yt-dlp runs deno for YouTube's challenge, and deno needs a
        # writable cache of its own.
        os.environ.setdefault("DENO_DIR", str(config.cache_dir / "deno"))
        return cls(impersonate=config.storage.impersonate,
                   cookies_from_browser=config.storage.cookies_from_browser,
                   cookies_file=config.storage.cookies_file,
                   cache_dir=config.cache_dir / "yt-dlp")

    @property
    def last_error(self) -> BackendError | None:
        """Error from the most recent call made on the current thread."""
        return getattr(self._local, "last_error", None)

    @last_error.setter
    def last_error(self, value: BackendError | None) -> None:
        self._local.last_error = value

    def classify_error(self, error: Exception) -> BackendError:
        """Classify a yt-dlp exception into a structured error."""
        msg = str(error)
        details = msg

        if "429" in msg or "too many requests" in msg.lower():
            return BackendError(
                error_type=BackendErrorType.RATE_LIMITED,
                message="The source asked the box to slow down (too many requests).",
                details=details,
                suggestion="Wait an hour or so, then try again.",
            )
        if "Sign in to confirm" in msg or re.search(r"\bbots?\b", msg.lower()):
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
        if any(w in msg.lower() for w in ("unavailable", "not available", "private", "removed")):
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
        if "incomplete youtube id" in msg.lower() or "unsupported url" in msg.lower():
            return BackendError(
                error_type=BackendErrorType.UNKNOWN,
                message="That link looks incomplete, or isn't a link to a video.",
                details=details,
                suggestion="Copy the whole address again from the video's page.",
            )
        return BackendError(
            error_type=BackendErrorType.UNKNOWN,
            message="An unexpected error occurred.",
            details=details,
            suggestion="Check the server logs for details.",
        )

    def _base_opts(self) -> dict:
        """Common yt-dlp options shared across all operations.

        Anti-bot measures, best first: browser TLS impersonation (no
        login, works headless), a cookie file, then cookies read from a
        local browser (desktop only).
        """
        opts: dict[str, Any] = {"quiet": True, "no_warnings": True}
        if self.cache_dir is not None:
            opts["cachedir"] = str(self.cache_dir)
        if self.impersonate:
            try:
                from yt_dlp.networking.impersonate import ImpersonateTarget
                opts["impersonate"] = ImpersonateTarget.from_str(self.impersonate)
            except Exception as e:
                logger.info("Browser impersonation unavailable (%s); continuing without it", e)
        if self.cookies_file:
            cookiefile = self._writable_cookies()
            if cookiefile:
                opts["cookiefile"] = cookiefile
        elif self.cookies_from_browser:
            opts["cookiesfrombrowser"] = (self.cookies_from_browser,)
        return opts

    def _writable_cookies(self) -> str:
        """The cookies file to give yt-dlp: a private copy in the cache
        folder, made again whenever the parent's file changes, because
        yt-dlp writes cookies back to its file when it finishes. '' when
        the parent's file cannot be read."""
        source = Path(self.cookies_file)
        if self.cache_dir is None:
            return str(source)
        copy = self.cache_dir / "cookies.txt"
        try:
            st = source.stat()
            signature = (st.st_mtime_ns, st.st_size)
            if signature != self._cookies_copied_from or not copy.exists():
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                tmp = copy.with_name(f"cookies.{threading.get_ident()}.tmp")
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, "wb") as f:
                    f.write(source.read_bytes())
                os.replace(tmp, copy)
                self._cookies_copied_from = signature
        except OSError as e:
            logger.warning("Cookie file %s cannot be read (%s); going on without cookies",
                           self.cookies_file, e)
            return ""
        return str(copy)

    def _blocked(self, url: str) -> bool:
        """True (with last_error set) when url points at the home network
        or this box. Lists shared by other people can contain such links,
        and opening one could change a router's or NAS's settings."""
        if not url.startswith(("http://", "https://")):
            return False
        try:
            check_url(url)
        except BlockedURL as e:
            if str(e).startswith("Cannot resolve"):
                return False
            self.last_error = BackendError(
                error_type=BackendErrorType.BLOCKED,
                message="This link points to a device on your home network, so Curatables won't open it.",
                details=str(e),
                suggestion="Use a link to the video's page on the internet.")
            logger.warning("refused a home-network URL: %s", url)
            return True
        except OSError:
            return False
        return False

    def fetch_video_info(self, url: str) -> VideoMetadata | None:
        import yt_dlp

        self.last_error = None
        if self._blocked(url):
            return None
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
        self.last_error = None
        # A bare "@handle" means a YouTube channel, whose uploads are
        # listed under /videos; other platforms' URLs are left alone.
        if not channel_url.startswith("http"):
            channel_url = f"https://www.youtube.com/{channel_url}"
        if "youtube.com" in channel_url and "/videos" not in channel_url:
            channel_url = channel_url.rstrip("/") + "/videos"
        if self._blocked(channel_url):
            return "Unknown", []
        info = self._flat(channel_url, max_results)
        if info is None:
            return "Unknown", []

        title = (info.get("channel") or info.get("uploader")
                 or info.get("title") or "Unknown")
        extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
        return title, _entries_to_metadata(info.get("entries", []), title,
                                           parent_extractor=extractor)

    def fetch_playlist(self, playlist_url: str,
                       max_results: int = 50) -> tuple[str, list[VideoMetadata]]:
        self.last_error = None
        if self._blocked(playlist_url):
            return "Unknown", []
        info = self._flat(playlist_url, max_results)
        if info is None:
            return "Unknown", []

        title = info.get("title") or "Unknown Playlist"
        extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
        return title, _entries_to_metadata(info.get("entries", []),
                                           parent_extractor=extractor)

    def _flat(self, url: str, limit: int) -> dict | None:
        """A flat (no per-video requests) listing of up to `limit`
        entries, or None on error with last_error set."""
        import yt_dlp

        self.last_error = None
        opts = {**self._base_opts(), "extract_flat": True, "skip_download": True,
                "playlistend": limit}
        with yt_dlp.YoutubeDL(opts) as ydl:
            try:
                return ydl.extract_info(url, download=False) or {}
            except Exception as e:
                self.last_error = self.classify_error(e)
                logger.warning("[%s] %s", self.last_error.error_type.value, self.last_error.message)
                logger.debug("  Details: %s", self.last_error.details)
                return None

    def search(self, query: str, max_results: int = 12,
               newest_first: bool = False) -> list[VideoMetadata]:
        if newest_first:
            # Videos only, by upload date (yt-dlp has no search prefix for it).
            url = ("https://www.youtube.com/results?sp=CAISAhAB&search_query="
                   + quote_plus(query))
        else:
            url = f"ytsearch{int(max_results)}:{query}"
        info = self._flat(url, int(max_results))
        return _entries_to_metadata((info or {}).get("entries", []), parent_extractor="youtube")

    def search_channels(self, query: str, max_results: int = 12) -> list[ChannelHit]:
        url = ("https://www.youtube.com/results?sp=EgIQAg%3D%3D&search_query="
               + quote_plus(query))
        info = self._flat(url, int(max_results))
        hits = []
        for e in (info or {}).get("entries") or []:
            cid = str((e or {}).get("channel_id") or e.get("id") or "")
            if not _YOUTUBE_CHANNEL_ID.match(cid):
                continue
            thumbs = [t for t in e.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
            thumb = str(thumbs[-1]["url"]) if thumbs else ""
            if thumb.startswith("//"):
                thumb = "https:" + thumb
            hits.append(ChannelHit(cid, str(e.get("channel") or e.get("title") or ""),
                                   thumb, int(e.get("channel_follower_count") or 0)))
        return hits

    def download_video(self, url: str, output_dir: Path,
                       resolution: int = 720,
                       subtitle_langs: list[str] | None = None,
                       max_filesize: int | None = None,
                       keep_going: Callable[[], bool] | None = None) -> Path | None:
        import yt_dlp

        self.last_error = None
        if self._blocked(url):
            return None
        output_dir.mkdir(parents=True, exist_ok=True)

        # Prefer H.264 + AAC, which every client plays without a transcode.
        # VP9/AV1/Opus fall through to the last clauses and are transcoded
        # later by app/services/normalize.py.
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
            "noplaylist": True,
        }
        if max_filesize:
            opts["max_filesize"] = max_filesize
        if keep_going is not None:
            def _stop_when_asked(_status: dict) -> None:
                if not keep_going():
                    raise _StopDownload("stopped: the drive is full")
            opts["progress_hooks"] = [_stop_when_asked]

        if subtitle_langs:
            opts["writesubtitles"] = True
            opts["writeautomaticsub"] = True
            opts["subtitlesformat"] = "vtt"
            opts["subtitleslangs"] = subtitle_langs

        try:
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
            except yt_dlp.utils.DownloadError as e:
                # yt-dlp fails the whole video when one subtitle track
                # fails (YouTube often answers those with 429), so
                # fetch the video again without them.
                if not (subtitle_langs and _SUBTITLE_FAILED in str(e)):
                    raise
                logger.warning("Subtitles left out for %s: %s", url, e)
                opts = {k: v for k, v in opts.items() if k not in _SUBTITLE_OPTS}
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.download([url])
        except Exception as e:
            self.last_error = self.classify_error(e)
            logger.exception("Download failed for %s", url)
            return None

        # A single-format pull or a failed merge can leave video.webm or
        # video.mkv instead of video.mp4. Return the real file without
        # renaming it (that would lie about the codecs); the normalizer
        # turns it into a proper video.mp4.
        expected = output_dir / "video.mp4"
        if expected.exists():
            return expected
        for f in sorted(output_dir.iterdir()):
            if f.stem == "video" and f.suffix in (".webm", ".mkv", ".mov", ".m4v"):
                return f
        if max_filesize:
            self.last_error = BackendError(
                error_type=BackendErrorType.FORMAT,
                message="No file was downloaded. The video may be larger "
                        "than the download size limit.",
                details=f"max_filesize={max_filesize}",
                suggestion="Pick a lower resolution, or raise the maximum "
                           "download size in Settings.",
            )
        return None


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
    """Convert a yt-dlp info dict to VideoMetadata."""
    extractor = (info.get("extractor_key") or info.get("extractor") or "").lower()
    return VideoMetadata(
        video_id=info.get("id", ""),
        title=info.get("title") or "Unknown",
        channel=info.get("channel") or info.get("uploader") or "Unknown",
        duration=_parse_duration(info.get("duration")),
        view_count=info.get("view_count") or 0,
        upload_date=info.get("upload_date") or "",
        description=info.get("description") or "",
        thumbnail_url=_thumbnail(info, extractor, full=True),
        extractor=extractor,
        original_url=info.get("webpage_url") or info.get("url") or "",
        is_live=_is_live(info),
    )


def _is_live(info: dict) -> bool:
    return bool(info.get("is_live")) or info.get("live_status") in ("is_live", "is_upcoming")


def _thumbnail(info: dict, extractor: str, full: bool = False) -> str:
    """The picture to download for a video. For YouTube that is a plain
    JPEG: search and playlist entries list signed thumbnail links that
    YouTube answers with AVIF or WebP, which old tablets can't show and
    the thumbnail check refuses. hqdefault.jpg exists for every video."""
    vid = str(info.get("id") or "")
    youtube = ((extractor.startswith("youtube") or str(info.get("ie_key") or "").lower() == "youtube")
               and _YOUTUBE_VIDEO_ID.match(vid))
    url = str(info.get("thumbnail") or "")
    if not url and not full:
        thumbs = [t for t in info.get("thumbnails") or [] if isinstance(t, dict) and t.get("url")]
        url = str(thumbs[-1]["url"]) if thumbs else ""
    if youtube:
        parts = urlsplit(url)
        if not (full and parts.path.endswith(".jpg") and not parts.query):
            return f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    return url


def _entries_to_metadata(entries: list,
                         default_channel: str = "Unknown",
                         parent_extractor: str = "") -> list[VideoMetadata]:
    """Convert a yt-dlp entries list to VideoMetadata.

    Flat extraction can omit an entry's extractor, so the containing
    channel or playlist's `parent_extractor` is the fallback. Entries
    whose IDs are unsafe as path components are skipped, and so are
    YouTube videos that are gone (private or deleted): they come
    without a title.
    """
    results = []
    for e in entries:
        if not e:
            continue
        vid_id = e.get("id", "")
        if not vid_id or not _SAFE_ENTRY_ID.match(vid_id):
            continue
        # ie_key names the video's own extractor; a channel listing's
        # extractor is "youtubetab", which must not end up in video ids.
        extractor = (e.get("extractor_key") or e.get("extractor") or e.get("ie_key")
                     or parent_extractor or "").lower()
        title = e.get("title")
        if title in _GONE_TITLES or (not title and extractor.startswith("youtube")):
            continue
        results.append(VideoMetadata(
            video_id=vid_id,
            title=title or "Untitled video",
            channel=e.get("channel") or e.get("uploader") or default_channel,
            duration=_parse_duration(e.get("duration")),
            view_count=e.get("view_count") or 0,
            upload_date=e.get("upload_date") or "",
            description=e.get("description") or "",
            thumbnail_url=_thumbnail(e, extractor),
            extractor=extractor,
            original_url=e.get("webpage_url") or e.get("url") or "",
            is_live=_is_live(e),
        ))
    return results
