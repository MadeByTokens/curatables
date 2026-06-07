from __future__ import annotations
"""Content service — business logic for managing approved content."""

import logging
import threading
from datetime import datetime, timedelta

from pathlib import Path

logger = logging.getLogger(__name__)

from app.config import Config
from app.models import Video, ViewerContext
from app.repositories import VideoRepository, SourceRepository, ChannelRepository
from app.models.source import Source
from app.backends.base import VideoMetadata
from app.services.video_source import VideoSourceService, ParsedURL
from app.services.thumbnails import ThumbnailService
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.ids import make_video_id
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

    def _kid_visible_channel_ids(self, viewer: ViewerContext) -> list[int]:
        """Effective set of channel IDs visible to a kid viewer.

        Union of parent-created channels (respecting whitelist) and
        channels owned by this kid profile. Sibling-owned channels are
        not included unless the parent explicitly whitelisted them.
        """
        return self.channel_repo.list_visible_to(
            viewer.profile_id, viewer.allowed_channel_ids,
        )

    def get_video_for_viewer(self, video_id: str, viewer: ViewerContext) -> Video:
        """Get a video if the viewer is allowed to see it and it's ready."""
        video = self.get_video(video_id)
        if video.status != "active" or video.download_status != "ready":
            raise VideoNotFound(f"Video {video_id} not available")

        if viewer.is_child:
            visible = self._kid_visible_channel_ids(viewer)
            if video.channel_id is None or video.channel_id not in visible:
                raise VideoNotFound(f"Video {video_id} not available")
        elif not viewer.can_see_channel(video.channel_id):
            raise VideoNotFound(f"Video {video_id} not available")
        return video

    def list_for_viewer(self, viewer: ViewerContext,
                        page: int = 1, per_page: int = 24,
                        channel_id: int | None = None) -> tuple[list[Video], int]:
        """List videos the viewer can see (active + downloaded only)."""
        offset = (page - 1) * per_page

        # Kid viewers always go through the visible-channels union so
        # their own owned channels and parent channels are both shown,
        # while sibling-owned channels stay hidden.
        if viewer.is_child:
            visible_ids = self._kid_visible_channel_ids(viewer)
            if channel_id is not None:
                if channel_id not in visible_ids:
                    return [], 0
                videos = self.video_repo.list_ready(
                    channel_id=channel_id, limit=per_page, offset=offset)
                total = self.video_repo.count_ready(channel_id=channel_id)
            else:
                videos = self.video_repo.list_ready_by_channels(
                    visible_ids, limit=per_page, offset=offset)
                total = self.video_repo.count_ready_by_channels(visible_ids)
            return videos, total

        if channel_id is not None:
            # Verify restricted viewer is allowed to see this channel
            if not viewer.can_see_channel(channel_id):
                return [], 0
            videos = self.video_repo.list_ready(
                channel_id=channel_id, limit=per_page, offset=offset)
            total = self.video_repo.count_ready(channel_id=channel_id)
        elif viewer.allowed_channel_ids is not None:
            videos = self.video_repo.list_ready_by_channels(
                viewer.allowed_channel_ids, limit=per_page, offset=offset)
            total = self.video_repo.count_ready_by_channels(
                viewer.allowed_channel_ids)
        else:
            videos = self.video_repo.list_ready(limit=per_page, offset=offset)
            total = self.video_repo.count_ready()

        return videos, total

    def search_for_viewer(self, query: str, viewer: ViewerContext,
                          page: int = 1, per_page: int = 24) -> list[Video]:
        """Search within approved content visible to the viewer.
        Returns empty list if search is disabled for this viewer."""
        if viewer.search_mode == "disabled":
            return []
        offset = (page - 1) * per_page
        if viewer.is_child:
            visible_ids = self._kid_visible_channel_ids(viewer)
            return self.video_repo.search_ready(
                query, channel_ids=visible_ids,
                limit=per_page, offset=offset)
        return self.video_repo.search_ready(
            query, channel_ids=viewer.allowed_channel_ids,
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

    def list_all(self, page: int = 1, per_page: int = 30) -> tuple[list[Video], int]:
        """List all videos (for parent dashboard), any status."""
        offset = (page - 1) * per_page
        videos = self.video_repo.list(status=None, limit=per_page, offset=offset)
        total = self.video_repo.count(status=None)
        return videos, total

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
            for v in videos:
                self.thumbnails.download(
                    make_video_id(v.extractor, v.video_id),
                    v.thumbnail_url,
                )
            return title, videos

        elif parsed.url_type == "playlist":
            title, videos = self.source.fetch_playlist(parsed.clean_url)
            if not videos:
                err = self.get_last_backend_error()
                if err:
                    raise ContentError(f"{err.message}\n{err.suggestion}" if err.suggestion else err.message)
                raise ContentError("Could not fetch playlist")
            for v in videos:
                self.thumbnails.download(
                    make_video_id(v.extractor, v.video_id),
                    v.thumbnail_url,
                )
            return title, videos

        raise ContentError(f"Unknown URL type: {parsed.url_type}")

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
                        resolution: str) -> None:
        """Launch a background thread to download a video.
        The thread opens its own SQLite connection to avoid sharing
        the request-scoped connection across threads."""
        db_path = get_db_path(self.config.data_dir)
        storage = self.storage
        thumbnails = self.thumbnails
        subtitle_langs = self.config.storage.subtitle_langs
        cache_days = self.config.storage.cache_days
        metrics = self.metrics

        def _download():
            from app.db.connection import create_connection
            conn = create_connection(db_path)
            repo = VideoRepository(conn)
            try:
                repo.update(video_id, download_status="downloading")
                path = storage.download(
                    video_id, original_url, resolution,
                    subtitle_langs=subtitle_langs)
                if path:
                    now = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
                    if cache_days > 0:
                        expires = (datetime.utcnow() + timedelta(
                            days=cache_days)).strftime("%Y-%m-%d %H:%M:%S")
                    else:
                        expires = None
                    repo.update(
                        video_id,
                        download_status="ready",
                        cached_at=now,
                        cache_expires_at=expires,
                        file_size=storage.file_size(video_id),
                    )
                    # Extract first frame as thumbnail if none exists
                    if not thumbnails.is_cached(video_id):
                        thumbnails.extract_frame(video_id)
                    metrics.record_download("success")
                else:
                    # Check if backend has a classified error
                    backend_err = storage.backend.last_error
                    if backend_err:
                        error_msg = f"[{backend_err.error_type.value}] {backend_err.message}"
                    else:
                        error_msg = "Download returned no file"
                    repo.update(
                        video_id,
                        download_status="error",
                        download_error=error_msg,
                    )
                    metrics.record_download("failure")
            except InsufficientDiskSpace as e:
                logger.warning("Disk quota blocked download of %s: %s", video_id, e)
                try:
                    repo.update(
                        video_id,
                        download_status="error",
                        download_error=f"[disk_full] {str(e)[:500]}",
                    )
                except Exception:
                    logger.exception("Failed to record disk-full error for %s", video_id)
                metrics.record_download("disk_full")
            except Exception as e:
                logger.exception("Download thread error for %s", video_id)
                try:
                    repo.update(
                        video_id,
                        download_status="error",
                        download_error=f"[unknown] {str(e)[:500]}",
                    )
                except Exception:
                    logger.exception("Failed to record download error for %s", video_id)
                metrics.record_download("failure")
            finally:
                conn.close()

        thread = threading.Thread(target=_download, daemon=True)
        thread.start()

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

    def update_video(self, video_id: str, **fields) -> Video:
        self.video_repo.update(video_id, **fields)
        return self.get_video(video_id)

    def try_rehydrate_evicted(self, video_id: str, viewer: ViewerContext) -> Video | None:
        """If the video is evicted and the viewer is allowed to see it,
        flip it back to 'pending' and queue a re-download.

        Returns the updated Video on success, else None.

        This is the on-demand counterpart to the cache sweeper: the
        kid tapping an evicted bookmark pays a download wait, but the
        disk stayed free in the meantime. Skips the action if the
        video isn't evicted, isn't visible to this viewer, or its
        channel isn't accessible.
        """
        video = self.video_repo.get(video_id)
        if not video or video.download_status != "evicted":
            return None
        if video.status != "active":
            return None
        if viewer.is_child:
            visible = self._kid_visible_channel_ids(viewer)
            if video.channel_id is None or video.channel_id not in visible:
                return None
        elif not viewer.can_see_channel(video.channel_id):
            return None
        self.video_repo.update(video_id, download_status="pending")
        self._start_download(video_id, video.original_url, video.resolution)
        video.download_status = "pending"
        return video

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

    def list_channels(self):
        return self.channel_repo.list()

    def resolve_channel(self, channel_id_str: str,
                        new_channel_name: str) -> int | None:
        """Resolve a channel selection: create new or use existing."""
        if new_channel_name.strip():
            return self.channel_repo.create(new_channel_name.strip())
        if channel_id_str and channel_id_str.isdigit():
            return int(channel_id_str)
        return None
