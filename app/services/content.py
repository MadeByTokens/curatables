"""Content service — business logic for managing approved content."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import NoReturn

from app.config import Config
from app.models import Channel, Video, ViewerContext
from app.repositories import VideoRepository, SourceRepository, ChannelRepository
from app.models.source import Source
from app.backends.base import VideoMetadata
from app.services.video_source import VideoSourceService, ParsedURL
from app.services.thumbnails import ThumbnailService
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.download_queue import get_download_queue
from app.services import download_control
from app.services.ffmpeg_runner import Cancelled
from app.services.ids import make_video_id
from app.services.search_gate import GATE
from app.db.connection import create_connection, get_db_path
from app.services.metrics import MetricsService

logger = logging.getLogger(__name__)

# Downloads the parent paused (partial file kept) or stopped.
PAUSED_OR_STOPPED = ("paused", "stopped")


class ContentError(Exception):
    """Raised when a content operation fails."""


class VideoNotFound(ContentError):
    pass


class ContentService:
    def __init__(
        self,
        video_repo: VideoRepository,
        source_repo: SourceRepository,
        channel_repo: ChannelRepository,
        source: VideoSourceService,
        thumbnails: ThumbnailService,
        storage: StorageService,
        config: Config,
        metrics: MetricsService | None = None,
    ):
        self.video_repo = video_repo
        self.source_repo = source_repo
        self.channel_repo = channel_repo
        self.source = source
        self.thumbnails = thumbnails
        self.storage = storage
        self.config = config
        self.metrics = metrics or MetricsService(enabled=False)

    # --- Reading ---

    def get_video(self, video_id: str) -> Video:
        video = self.video_repo.get(video_id)
        if not video:
            raise VideoNotFound(f"Video {video_id} not found")
        return video

    def visible_channel_ids(self, viewer: ViewerContext) -> list[int] | None:
        """Channel IDs whose videos the viewer may watch.

        None means every channel, including uncategorized videos, and is
        returned only for the parent. A kid gets the parent-created
        channels allowed by their profile plus the channels they own;
        sibling-owned channels only when explicitly whitelisted.
        Anyone else gets an empty list.
        """
        if viewer.is_parent:
            return None
        if viewer.is_child and viewer.profile_id:
            return self.channel_repo.list_visible_to(viewer.profile_id)
        return []

    def can_view(self, video: Video, viewer: ViewerContext) -> bool:
        visible = self.visible_channel_ids(viewer)
        if visible is None:
            return True
        return video.channel_id is not None and video.channel_id in visible

    def channels_for_viewer(self, viewer: ViewerContext) -> list[Channel]:
        visible = self.visible_channel_ids(viewer)
        channels = self.channel_repo.list()
        if visible is None:
            return channels
        allowed = set(visible)
        return [c for c in channels if c.id in allowed]

    def channels_to_browse(self, viewer: ViewerContext) -> list[Channel]:
        """The channel buttons a kid sees: channels with something to
        watch, and the kid's own channels (they hold the kid's saved
        videos). An empty channel can still be picked when asking for a
        new video."""
        with_videos = self.video_repo.channel_ids_with_ready(self._hidden_for(viewer))
        return [c for c in self.channels_for_viewer(viewer)
                if c.id in with_videos
                or (viewer.profile_id and c.owner_profile_id == viewer.profile_id)]

    @staticmethod
    def _hidden_for(viewer: ViewerContext) -> int | None:
        """The kid whose hidden videos are left out of lists, if any."""
        return viewer.profile_id if viewer.is_child else None

    def hidden_by_viewer(self, video: Video, viewer: ViewerContext) -> bool:
        kid = self._hidden_for(viewer)
        return kid is not None and self.video_repo.is_hidden_for(video.video_id, kid)

    def get_video_for_viewer(self, video_id: str, viewer: ViewerContext) -> Video:
        """Get a video if the viewer is allowed to see it and it's ready.
        A video the kid hid is gone for them until a grown-up shows it
        again."""
        video = self.get_video(video_id)
        if video.status != "active" or video.download_status != "ready":
            raise VideoNotFound(f"Video {video_id} not available")
        if not self.can_view(video, viewer) or self.hidden_by_viewer(video, viewer):
            raise VideoNotFound(f"Video {video_id} not available")
        return video

    def list_for_viewer(self, viewer: ViewerContext,
                        page: int = 1, per_page: int = 24,
                        channel_id: int | None = None) -> tuple[list[Video], int]:
        """List videos the viewer can see (active + downloaded only)."""
        offset = (page - 1) * per_page
        visible = self.visible_channel_ids(viewer)
        hidden_for = self._hidden_for(viewer)

        if channel_id is not None:
            if visible is not None and channel_id not in visible:
                return [], 0
            videos = self.video_repo.list_ready(
                channel_id=channel_id, limit=per_page, offset=offset, hidden_for=hidden_for)
            total = self.video_repo.count_ready(channel_id=channel_id, hidden_for=hidden_for)
        elif visible is None:
            videos = self.video_repo.list_ready(limit=per_page, offset=offset,
                                                hidden_for=hidden_for)
            total = self.video_repo.count_ready(hidden_for=hidden_for)
        else:
            videos = self.video_repo.list_ready_by_channels(
                visible, limit=per_page, offset=offset, hidden_for=hidden_for)
            total = self.video_repo.count_ready_by_channels(visible, hidden_for=hidden_for)
        return videos, total

    def search_for_viewer(self, query: str, viewer: ViewerContext,
                          page: int = 1, per_page: int = 24,
                          close: bool = False) -> list[Video]:
        """Search within approved content visible to the viewer; with
        `close`, for words spelled close to the query's.
        Returns empty list if search is disabled for this viewer."""
        if viewer.search_mode == "disabled":
            return []
        offset = (page - 1) * per_page
        search = self.video_repo.search_ready_close if close else self.video_repo.search_ready
        return search(
            query, channel_ids=self.visible_channel_ids(viewer),
            limit=per_page, offset=offset,
            profile_id=viewer.profile_id if viewer.is_child else None)

    def fetch_previews_for_urls(self, urls: list[str]) -> tuple[list[VideoMetadata], list[str]]:
        """Fetch metadata previews for a shared-curation import.

        Returns (videos, failed_urls). Playlist and channel URLs are
        expanded into the same flat list; duplicate URLs are fetched once.
        """
        videos: list[VideoMetadata] = []
        failed: list[str] = []
        seen: set[str] = set()
        for raw in urls:
            url = (raw or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            parsed = self.parse_url(url)
            if not parsed:
                failed.append(url)
                continue
            try:
                _src_title, vs = self.fetch_preview(parsed)
            except ContentError:
                failed.append(url)
                continue
            videos.extend(vs)
        return videos, failed

    def list_all(self, page: int = 1, per_page: int = 30,
                 filter_name: str = "", sort: str = "newest") -> tuple[list[Video], int]:
        """List videos for the parent content page, any status, optionally
        narrowed to one of VideoRepository.LIST_FILTERS and ordered by one
        of VideoRepository.SORTS."""
        offset = (page - 1) * per_page
        if filter_name in self.video_repo.LIST_FILTERS:
            return (self.video_repo.list_filtered(filter_name, per_page, offset, sort=sort),
                    self.video_repo.count_filtered(filter_name))
        videos = self.video_repo.list(status=None, limit=per_page, offset=offset, sort=sort)
        total = self.video_repo.count(status=None)
        return videos, total

    def download_statuses(self, video_ids: list[str]) -> dict[str, str]:
        return self.video_repo.download_statuses(video_ids)

    def retry_download(self, video_id: str) -> bool:
        """Queue a failed, paused or stopped download again (a paused one
        carries on from its partial file). Returns False for any other
        state."""
        video = self.video_repo.get(video_id)
        if (video is None or video.storage_mode == "uploaded"
                or video.download_status not in ("error", *PAUSED_OR_STOPPED)):
            return False
        download_control.clear(video_id)
        self.video_repo.update(video_id, download_status="pending", download_error="")
        self._start_download(video_id, video.original_url, video.resolution)
        return True

    def pause_download(self, video_id: str) -> bool:
        """Pause a waiting or running download; its partial file stays."""
        video = self.video_repo.get(video_id)
        if video is None or video.download_status not in ("pending", "downloading"):
            return False
        if video.download_status == "downloading":
            download_control.request(video_id, download_control.PAUSE)
        # A running job sees the flag at its next progress tick and ends
        # paused; one still queued sees the status when it starts.
        self.video_repo.update(video_id, download_status="paused", download_error="")
        return True

    def pause_all_downloads(self) -> int:
        return sum(self.pause_download(v)
                   for v in self.video_repo.ids_by_download_status("pending", "downloading"))

    def resume_all_downloads(self) -> int:
        return sum(self.retry_download(v)
                   for v in self.video_repo.ids_by_download_status("paused"))

    def download_counts(self) -> dict[str, int]:
        """How many downloads wait or run ("active") and how many are paused."""
        return {"active": len(self.video_repo.ids_by_download_status("pending", "downloading")),
                "paused": len(self.video_repo.ids_by_download_status("paused"))}

    def stop_download(self, video_id: str) -> bool:
        """Stop a download for good: its partial files go, the video
        stays listed so it can be downloaded again."""
        video = self.video_repo.get(video_id)
        if video is None or video.download_status not in ("pending", "downloading", "paused"):
            return False
        if video.download_status == "downloading":
            download_control.request(video_id, download_control.STOP)
        else:
            self.storage.remove_partial_download(video_id)
        self.video_repo.update(video_id, download_status="stopped", download_error="")
        return True

    # --- Writing ---

    def parse_url(self, url: str) -> ParsedURL | None:
        return self.source.parse_url(url)

    def get_last_backend_error(self):
        """Get the last classified error from the backend, if any."""
        return self.source.backend.last_error

    def fetch_preview(self, parsed: ParsedURL) -> tuple[str, list[VideoMetadata]]:
        """Fetch metadata from the source for preview before adding.

        Works for any platform yt-dlp supports: the ParsedURL arrives
        pre-classified (video / channel / playlist, via the fast-path
        regex) or as a catch-all "video" that hands the raw URL
        straight to the backend.
        """
        if parsed.url_type == "video":
            if parsed.clean_url == f"https://www.youtube.com/watch?v={parsed.resource_id}":
                info = GATE.video(parsed.resource_id,
                                  lambda: self.source.fetch_video_info(parsed.clean_url))
            else:
                info = self.source.fetch_video_info(parsed.clean_url)
            if not info:
                self._raise_backend_error("Could not fetch video info")
            self.thumbnails.download(
                make_video_id(info.extractor, info.video_id),
                info.thumbnail_url,
            )
            return info.title, [info]

        elif parsed.url_type == "channel":
            title, videos = GATE.listing(("channel-listing", parsed.clean_url),
                                         lambda: self.source.fetch_channel_videos(parsed.clean_url))
            if not videos:
                self._raise_backend_error("Could not fetch channel videos")
            self._download_thumbnails(videos)
            return title, videos

        elif parsed.url_type == "playlist":
            title, videos = GATE.listing(("playlist-listing", parsed.clean_url),
                                         lambda: self.source.fetch_playlist(parsed.clean_url))
            if not videos:
                self._raise_backend_error("Could not fetch playlist")
            self._download_thumbnails(videos)
            return title, videos

        raise ContentError(f"Unknown URL type: {parsed.url_type}")

    def _raise_backend_error(self, fallback: str) -> NoReturn:
        err = self.get_last_backend_error()
        if err is None:
            raise ContentError(fallback)
        raise ContentError(f"{err.message}\n{err.suggestion}" if err.suggestion else err.message)

    def _download_thumbnails(self, videos: list[VideoMetadata]) -> None:
        self.thumbnails.download_many([
            (make_video_id(v.extractor, v.video_id), v.thumbnail_url)
            for v in videos if v.thumbnail_url
        ])

    def add_video(self, info: VideoMetadata, source_id: int | None = None,
                  channel_id: int | None = None,
                  resolution: str | None = None,
                  title_override: str | None = None,
                  description_override: str | None = None) -> Video | None:
        """Add a video and start downloading it in the background.
        Returns None if the video already exists.

        Accepts a `VideoMetadata` with the raw yt-dlp `video_id`, the
        `extractor` key, and the `original_url`. Stores the composite
        `{extractor}_{raw_id}` form in the DB, which keeps the
        filesystem path and route URLs safe for any platform.
        """
        stored_id = make_video_id(info.extractor, info.video_id)
        existing = self.video_repo.get(stored_id)
        if existing:
            return None

        res = resolution or self.config.storage.default_resolution
        video = Video(
            video_id=stored_id,
            title=title_override or info.title,
            original_title=info.title,
            extractor=(info.extractor or "").lower(),
            original_url=info.original_url,
            channel_name=info.channel,
            description=description_override or info.description,
            duration=info.duration,
            upload_date=info.upload_date,
            view_count=info.view_count,
            thumbnail_url=info.thumbnail_url,
            source_id=source_id,
            channel_id=channel_id,
            resolution=res,
            download_status="pending",
        )
        self.video_repo.insert(video)
        self._start_download(stored_id, info.original_url, res)
        return video

    def _start_download(self, video_id: str, original_url: str,
                        resolution: str, recheck: bool = False) -> None:
        """Queue a background download on the shared DownloadQueue.
        recheck=True (re-downloads after eviction) compares the new file
        with what the parent approved."""
        queue = get_download_queue(self.config.storage.max_concurrent_downloads)
        queue.submit(video_id, lambda: self._run_download(
            video_id, original_url, resolution, recheck))

    def _content_changed(self, approved_seconds: int, path) -> bool:
        """True when a re-downloaded file's length differs from the length
        the parent approved by more than 10 s and 5 %: the page behind the
        URL now serves something else."""
        normalizer = getattr(self.storage, "normalizer", None)
        if not approved_seconds or normalizer is None:
            return False
        try:
            seconds = normalizer.probe.probe(path).duration_seconds or 0
        except Exception:
            return False
        diff = abs(seconds - approved_seconds)
        return bool(diff > 10 and diff > 0.05 * approved_seconds)

    def _run_download(self, video_id: str, original_url: str,
                      resolution: str, recheck: bool = False) -> None:
        """Download job body. Runs on a queue worker with its own SQLite
        connection; the request that queued it is long gone."""
        conn = create_connection(get_db_path(self.config.data_dir))
        repo = VideoRepository(conn)
        storage = self.storage
        try:
            approved = repo.get(video_id)
            if approved is None or approved.download_status in PAUSED_OR_STOPPED:
                # Paused or stopped while it waited in the queue.
                download_control.clear(video_id)
                return
            repo.update(video_id, download_status="downloading")
            try:
                path = storage.download(
                    video_id, original_url, resolution,
                    subtitle_langs=self.config.storage.subtitle_langs,
                    cancel=download_control.RequestToken(video_id))
            except Cancelled:
                path = None
            if repo.get(video_id) is None:
                download_control.clear(video_id)
                storage.delete_video_files(Video(video_id=video_id, title="", original_title=""))
                return
            asked = download_control.take(video_id)
            if asked == download_control.STOP:
                storage.remove_partial_download(video_id)
                repo.update(video_id, download_status="stopped", download_error="")
                return
            if asked == download_control.PAUSE and not path:
                repo.update(video_id, download_status="paused", download_error="")
                return
            if path:
                fields = {
                    "download_status": "ready",
                    "download_error": "",
                    "cached_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                    "file_size": storage.file_size(video_id),
                }
                if recheck and self._content_changed(approved.duration, path):
                    logger.warning("Re-download of %s differs from the approved "
                                   "video; holding it for review", video_id)
                    fields["status"] = "review"
                    fields["download_error"] = ("Changed since you approved it: "
                                                "the length is different. Watch it again.")
                repo.update(video_id, **fields)
                if not self.thumbnails.is_cached(video_id):
                    self.thumbnails.extract_frame(video_id, source_path=path)
                self.metrics.record_download("success")
            else:
                backend_err = storage.backend.last_error if storage.backend else None
                if backend_err:
                    error_msg = f"[{backend_err.error_type.value}] {backend_err.message}"
                else:
                    error_msg = "Download returned no file"
                repo.update(video_id, download_status="error",
                            download_error=error_msg)
                self.metrics.record_download("failure")
        except InsufficientDiskSpace as e:
            logger.warning("Disk quota blocked download of %s: %s", video_id, e)
            self._record_error(repo, video_id, f"[disk_full] {str(e)[:500]}")
            self.metrics.record_download("disk_full")
        except Exception as e:
            logger.exception("Download error for %s", video_id)
            self._record_error(repo, video_id, f"[unknown] {str(e)[:500]}")
            self.metrics.record_download("failure")
        finally:
            conn.close()

    @staticmethod
    def _record_error(repo: VideoRepository, video_id: str, message: str) -> None:
        try:
            repo.update(video_id, download_status="error", download_error=message)
        except Exception:
            logger.exception("Failed to record download error for %s", video_id)

    def create_source(self, source_type: str, extractor: str,
                      external_id: str, title: str, url: str) -> int:
        """Persist a Source row for a channel / playlist / single video.

        Uniqueness is enforced at the `(extractor, external_id)` level
        in the schema, so the same video ID can legitimately appear
        under different extractors without colliding.
        """
        source = Source(
            source_type=source_type,
            extractor=(extractor or "").lower() or "unknown",
            external_id=external_id,
            title=title,
            url=url,
        )
        return self.source_repo.create(source)

    def resume_pending_downloads(self) -> int:
        """Queue every 'pending' video (startup, after recover_from_crash
        has reset interrupted downloads). Returns how many were queued."""
        pending = self.video_repo.list_pending()
        for v in pending:
            self._start_download(v.video_id, v.original_url, v.resolution)
        return len(pending)

    def mark_played(self, video_id: str) -> None:
        self.video_repo.touch_played(video_id)

    def update_video(self, video_id: str, **fields) -> Video:
        self.video_repo.update(video_id, **fields)
        return self.get_video(video_id)

    def try_rehydrate_evicted(self, video_id: str, viewer: ViewerContext) -> Video | None:
        """Return the video if it is on its way to the viewer: an evicted
        video is flipped back to 'pending' and queued for re-download,
        and one already pending/downloading is returned as is.

        Returns None if the video doesn't exist, isn't active, isn't
        visible to this viewer, was hidden by them, or is in any other
        state (e.g. error).
        """
        video = self.video_repo.get(video_id)
        if (not video or video.status != "active" or not self.can_view(video, viewer)
                or self.hidden_by_viewer(video, viewer)):
            return None
        if video.download_status == "evicted":
            if self.video_repo.claim_evicted(video_id):
                self._start_download(video_id, video.original_url, video.resolution,
                                     recheck=True)
            video.download_status = "pending"
            return video
        if video.download_status in ("pending", "downloading"):
            if video.download_status == "pending" and video.storage_mode == "cache":
                self._start_download(video_id, video.original_url, video.resolution)
            return video
        return None

    def free_files(self, video_ids: list[str]):
        """See StorageService.free_files."""
        return self.storage.free_files(self.video_repo, video_ids)

    def hide_video(self, video_id: str) -> None:
        self.video_repo.update(video_id, status="hidden")

    def activate_video(self, video_id: str) -> None:
        self.video_repo.update(video_id, status="active")

    def delete_video(self, video_id: str) -> None:
        """Remove a video: files on disk first, then the DB row.

        Tolerant of missing files and missing video_id — the goal is
        to always end with no DB row and no orphan files. File
        cleanup errors are logged but never propagate, so a flaky
        filesystem can't block the delete.
        """
        video = self.video_repo.get(video_id)
        if video is not None:
            try:
                self.storage.delete_video_files(video)
            except Exception as e:
                logger.warning("Failed to clean up files for %s: %s", video_id, e)
            try:
                self.thumbnails.delete(video_id)
            except Exception as e:
                logger.warning("Failed to clean up thumbnail for %s: %s", video_id, e)
        self.video_repo.delete(video_id)

    # --- Channels ---

    def list_channel_videos(self, channel_id: int) -> list[Video]:
        """Every video in a channel, any status (for exports)."""
        return self.video_repo.list(status=None, channel_id=channel_id,
                                    limit=10_000, offset=0)

    def list_channels(self):
        return self.channel_repo.list()

    def already_in_library(self, videos: list[VideoMetadata]) -> dict[str, str]:
        """For each preview the library already has: its raw id and how the
        library shows it, the title and, when it has one, the channel."""
        found: dict[str, str] = {}
        for meta in videos:
            video = self.video_repo.get(make_video_id(meta.extractor, meta.video_id))
            if video is None:
                continue
            channel = self.channel_repo.get(video.channel_id) if video.channel_id else None
            found[meta.video_id] = f"{video.title} ({channel.name})" if channel else video.title
        return found

    def resolve_channel(self, channel_id_str: str,
                        new_channel_name: str) -> int | None:
        """Resolve a channel selection: create new or use existing."""
        name = new_channel_name.strip()
        if name:
            existing = self.channel_repo.get_by_name(name)
            if existing is not None and existing.owner_profile_id is not None:
                raise ContentError(
                    f'"{name}" is a kid\'s own channel. Pick another name.')
            return self.channel_repo.create(name)
        if channel_id_str and channel_id_str.isdigit():
            return int(channel_id_str)
        return None
