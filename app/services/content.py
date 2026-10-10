from __future__ import annotations
"""Content service — business logic for managing approved content."""

import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

from app.config import Config
from app.models import Channel, Video, ViewerContext
from app.repositories import VideoRepository, SourceRepository, ChannelRepository
from app.models.source import Source
from app.backends.base import VideoMetadata
from app.services.video_source import VideoSourceService, ParsedURL
from app.services.thumbnails import ThumbnailService
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.download_queue import get_download_queue
from app.services.ids import make_video_id
from app.services.search_gate import GATE
from app.db.connection import get_db_path


class ContentError(Exception):
    """Raised when a content operation fails."""
    pass


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
        metrics=None,
    ):
        self.video_repo = video_repo
        self.source_repo = source_repo
        self.channel_repo = channel_repo
        self.source = source
        self.thumbnails = thumbnails
        self.storage = storage
        self.config = config
        # Default to a disabled MetricsService so unit tests that build
        # ContentService directly (without going through FastAPI DI) do
        # not need to pass a metrics arg.
        if metrics is None:
            from app.services.metrics import MetricsService
            metrics = MetricsService(enabled=False)
        self.metrics = metrics

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

    def get_video_for_viewer(self, video_id: str, viewer: ViewerContext) -> Video:
        """Get a video if the viewer is allowed to see it and it's ready."""
        video = self.get_video(video_id)
        if video.status != "active" or video.download_status != "ready":
            raise VideoNotFound(f"Video {video_id} not available")
        if not self.can_view(video, viewer):
            raise VideoNotFound(f"Video {video_id} not available")
        return video

    def list_for_viewer(self, viewer: ViewerContext,
                        page: int = 1, per_page: int = 24,
                        channel_id: int | None = None) -> tuple[list[Video], int]:
        """List videos the viewer can see (active + downloaded only)."""
        offset = (page - 1) * per_page
        visible = self.visible_channel_ids(viewer)

        if channel_id is not None:
            if visible is not None and channel_id not in visible:
                return [], 0
            videos = self.video_repo.list_ready(
                channel_id=channel_id, limit=per_page, offset=offset)
            total = self.video_repo.count_ready(channel_id=channel_id)
        elif visible is None:
            videos = self.video_repo.list_ready(limit=per_page, offset=offset)
            total = self.video_repo.count_ready()
        else:
            videos = self.video_repo.list_ready_by_channels(
                visible, limit=per_page, offset=offset)
            total = self.video_repo.count_ready_by_channels(visible)
        return videos, total

    def search_for_viewer(self, query: str, viewer: ViewerContext,
                          page: int = 1, per_page: int = 24) -> list[Video]:
        """Search within approved content visible to the viewer.
        Returns empty list if search is disabled for this viewer."""
        if viewer.search_mode == "disabled":
            return []
        offset = (page - 1) * per_page
        return self.video_repo.search_ready(
            query, channel_ids=self.visible_channel_ids(viewer),
            limit=per_page, offset=offset)

    def fetch_previews_for_urls(self, urls: list[str]) -> tuple[list[VideoMetadata], list[str]]:
        """Fetch metadata previews for a batch of URLs (used by the
        shared-curation import flow).

        Returns (videos, failed_urls). URLs that parse_url can't
        recognise or that the backend can't resolve are silently
        skipped and reported in failed_urls; the rest flow into a
        single VideoMetadata list that the /parent/add preview page
        already knows how to render.

        Playlist / channel URLs are expanded — their contents are
        flattened into the same list — so an import can mix single
        videos with playlists in one file.
        """
        videos: list[VideoMetadata] = []
        failed: list[str] = []
        # Dedup URLs within the batch so a repeat line doesn't
        # double-preview (the /confirm step enforces uniqueness at
        # the DB layer, but pre-empting is cheaper).
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
                 filter_name: str = "") -> tuple[list[Video], int]:
        """List videos for the parent content page, any status, optionally
        narrowed to one of VideoRepository.LIST_FILTERS."""
        offset = (page - 1) * per_page
        if filter_name in self.video_repo.LIST_FILTERS:
            return (self.video_repo.list_filtered(filter_name, per_page, offset),
                    self.video_repo.count_filtered(filter_name))
        videos = self.video_repo.list(status=None, limit=per_page, offset=offset)
        total = self.video_repo.count(status=None)
        return videos, total

    def download_statuses(self, video_ids: list[str]) -> dict[str, str]:
        return self.video_repo.download_statuses(video_ids)

    def retry_download(self, video_id: str) -> bool:
        """Queue a failed download again. Returns False if the video is
        not in the error state."""
        video = self.video_repo.get(video_id)
        if video is None or video.download_status != "error" or video.storage_mode == "uploaded":
            return False
        self.video_repo.update(video_id, download_status="pending", download_error="")
        self._start_download(video_id, video.original_url, video.resolution)
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
                err = self.get_last_backend_error()
                if err:
                    raise ContentError(f"{err.message}\n{err.suggestion}" if err.suggestion else err.message)
                raise ContentError("Could not fetch video info")
            self.thumbnails.download(
                make_video_id(info.extractor, info.video_id),
                info.thumbnail_url,
            )
            return info.title, [info]

        elif parsed.url_type == "channel":
            title, videos = self.source.fetch_channel_videos(parsed.clean_url)
            if not videos:
                err = self.get_last_backend_error()
                if err:
                    raise ContentError(f"{err.message}\n{err.suggestion}" if err.suggestion else err.message)
                raise ContentError("Could not fetch channel videos")
            self._download_thumbnails(videos)
            return title, videos

        elif parsed.url_type == "playlist":
            title, videos = self.source.fetch_playlist(parsed.clean_url)
            if not videos:
                err = self.get_last_backend_error()
                if err:
                    raise ContentError(f"{err.message}\n{err.suggestion}" if err.suggestion else err.message)
                raise ContentError("Could not fetch playlist")
            self._download_thumbnails(videos)
            return title, videos

        raise ContentError(f"Unknown URL type: {parsed.url_type}")

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
        from app.db.connection import create_connection
        conn = create_connection(get_db_path(self.config.data_dir))
        repo = VideoRepository(conn)
        storage = self.storage
        try:
            approved = repo.get(video_id)
            if approved is None:
                return
            repo.update(video_id, download_status="downloading")
            path = storage.download(
                video_id, original_url, resolution,
                subtitle_langs=self.config.storage.subtitle_langs)
            if repo.get(video_id) is None:
                storage.delete_video_files(Video(video_id=video_id, title="", original_title=""))
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
        """Kick off background downloads for any videos stuck in 'pending'.
        Called once at server startup, after recover_from_crash has moved
        any interrupted 'downloading' rows back to 'pending'. Idempotent;
        safe to call when there are no pending rows. Returns the count
        of downloads queued."""
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
        visible to this viewer, or is in any other state (e.g. error).
        """
        video = self.video_repo.get(video_id)
        if not video or video.status != "active" or not self.can_view(video, viewer):
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
