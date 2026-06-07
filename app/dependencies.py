from __future__ import annotations
"""FastAPI dependency injection — the only place where layers connect."""

from fastapi import Depends, Request

from app.config import Config
from app.db.connection import create_connection, get_db_path
from app.models import ViewerContext
from app.repositories import (
    VideoRepository,
    ChannelRepository,
    SourceRepository,
    ProfileRepository,
    EventRepository,
)
from app.backends.base import VideoBackend
from app.backends.ytdlp import YtdlpBackend
from app.services.auth import AuthService
from app.services.content import ContentService
from app.services.video_source import VideoSourceService
from app.services.storage import StorageService
from app.services.storage_report import StorageReportService
from app.services.thumbnails import ThumbnailService
from app.services.media_probe import MediaProbeService
from app.services.normalize import MediaNormalizer
from app.services.uploads import UploadService
from app.services.relocation import RelocationService
from app.services.events import EventService
from app.services.profiles import ProfileService
from app.services.channels import ChannelService
from app.services.reactions import ReactionService
from app.services.comments import CommentService
from app.services.stats import StatsService
from app.services.kid_library import KidLibraryService
from app.repositories.reaction_repo import ReactionRepository
from app.repositories.comment_repo import CommentRepository
from app.repositories.override_repo import ProfileVideoOverrideRepository
from app.repositories.tag_repo import TagRepository
from app.repositories.profile_channel_video_repo import ProfileChannelVideoRepository


# --- Infrastructure ---

def get_db(request: Request):
    """Yield a fresh SQLite connection per request, closed automatically."""
    conn = create_connection(get_db_path(request.app.state.config.data_dir))
    try:
        yield conn
    finally:
        conn.close()


def get_config(request: Request) -> Config:
    return request.app.state.config


def get_metrics(request: Request):
    """Pull the live MetricsService off app.state.

    Always returns the singleton instance (enabled or not). Service
    constructors that take ``metrics=...`` should default to None and
    fall back to a disabled MetricsService when the dependency is not
    available — that path is exercised by unit tests that wire
    services directly without going through FastAPI's DI.
    """
    return request.app.state.metrics


# --- Repositories ---

def get_video_repo(db=Depends(get_db)) -> VideoRepository:
    return VideoRepository(db)


def get_channel_repo(db=Depends(get_db)) -> ChannelRepository:
    return ChannelRepository(db)


def get_source_repo(db=Depends(get_db)) -> SourceRepository:
    return SourceRepository(db)


def get_profile_repo(db=Depends(get_db)) -> ProfileRepository:
    return ProfileRepository(db)


def get_event_repo(db=Depends(get_db)) -> EventRepository:
    return EventRepository(db)


# --- Services ---

def get_backend(config=Depends(get_config)) -> VideoBackend:
    """Return the video backend. Change this to swap yt-dlp for another implementation."""
    return YtdlpBackend(
        impersonate=config.storage.impersonate,
        cookies_from_browser=config.storage.cookies_from_browser,
        cookies_file=config.storage.cookies_file,
    )


def get_auth_service(config=Depends(get_config)) -> AuthService:
    return AuthService(config)


def get_video_source_service(backend=Depends(get_backend)) -> VideoSourceService:
    return VideoSourceService(backend)


def get_thumbnail_service(config=Depends(get_config)) -> ThumbnailService:
    return ThumbnailService(config.data_dir)


# MediaProbeService is a process-wide singleton so the ffmpeg decoder
# enumeration only runs once per server process.
_probe_singleton: MediaProbeService | None = None


def get_probe_service() -> MediaProbeService:
    global _probe_singleton
    if _probe_singleton is None:
        _probe_singleton = MediaProbeService()
    return _probe_singleton


def get_storage_service(config=Depends(get_config),
                        backend=Depends(get_backend),
                        probe=Depends(get_probe_service)) -> StorageService:
    return StorageService(
        config.data_dir, backend,
        min_free_bytes=config.storage.min_free_disk_bytes,
        normalizer=MediaNormalizer(probe),
    )


def get_storage_report_service(
    config=Depends(get_config),
    video_repo=Depends(get_video_repo),
) -> StorageReportService:
    return StorageReportService(
        data_dir=config.data_dir,
        min_free_bytes=config.storage.min_free_disk_bytes,
        video_repo=video_repo,
    )


def get_upload_service(
    storage=Depends(get_storage_service),
    probe=Depends(get_probe_service),
    video_repo=Depends(get_video_repo),
    channel_repo=Depends(get_channel_repo),
    thumbnails=Depends(get_thumbnail_service),
) -> UploadService:
    return UploadService(storage, probe, video_repo, channel_repo, thumbnails)


def get_relocation_service(
    config=Depends(get_config),
    video_repo=Depends(get_video_repo),
) -> RelocationService:
    return RelocationService(config, video_repo)


def get_content_service(
    video_repo=Depends(get_video_repo),
    source_repo=Depends(get_source_repo),
    channel_repo=Depends(get_channel_repo),
    source=Depends(get_video_source_service),
    thumbnails=Depends(get_thumbnail_service),
    storage=Depends(get_storage_service),
    config=Depends(get_config),
    metrics=Depends(get_metrics),
) -> ContentService:
    return ContentService(
        video_repo, source_repo, channel_repo, source, thumbnails, storage, config,
        metrics=metrics,
    )


def get_event_service(event_repo=Depends(get_event_repo),
                      metrics=Depends(get_metrics)) -> EventService:
    return EventService(event_repo, metrics=metrics)


def get_profile_service(
    profile_repo=Depends(get_profile_repo),
) -> ProfileService:
    return ProfileService(profile_repo)


def get_channel_service(
    channel_repo=Depends(get_channel_repo),
) -> ChannelService:
    return ChannelService(channel_repo)


def get_reaction_repo(db=Depends(get_db)) -> ReactionRepository:
    return ReactionRepository(db)


def get_reaction_service(
    reaction_repo=Depends(get_reaction_repo),
    event_repo=Depends(get_event_repo),
) -> ReactionService:
    return ReactionService(reaction_repo, event_repo)


def get_comment_repo(db=Depends(get_db)) -> CommentRepository:
    return CommentRepository(db)


def get_override_repo(db=Depends(get_db)) -> ProfileVideoOverrideRepository:
    return ProfileVideoOverrideRepository(db)


def get_tag_repo(db=Depends(get_db)) -> TagRepository:
    return TagRepository(db)


def get_profile_channel_video_repo(db=Depends(get_db)) -> ProfileChannelVideoRepository:
    return ProfileChannelVideoRepository(db)


def get_comment_service(
    comment_repo=Depends(get_comment_repo),
    event_repo=Depends(get_event_repo),
) -> CommentService:
    return CommentService(comment_repo, event_repo)


def get_kid_library_service(
    override_repo=Depends(get_override_repo),
    tag_repo=Depends(get_tag_repo),
    channel_video_repo=Depends(get_profile_channel_video_repo),
    config=Depends(get_config),
) -> KidLibraryService:
    return KidLibraryService(override_repo, tag_repo, channel_video_repo,
                             config.data_dir)


def get_stats_service(
    event_repo=Depends(get_event_repo),
    comment_repo=Depends(get_comment_repo),
    reaction_repo=Depends(get_reaction_repo),
    profile_repo=Depends(get_profile_repo),
    comment_service=Depends(get_comment_service),
    video_repo=Depends(get_video_repo),
) -> StatsService:
    return StatsService(
        event_repo, comment_repo, reaction_repo, profile_repo,
        comment_service, video_repo=video_repo)


# --- Viewer context ---

def get_viewer(
    request: Request,
    profile_repo=Depends(get_profile_repo),
) -> ViewerContext:
    """Build a ViewerContext from the session.

    Priority: if a child profile is selected, use it — even if the
    parent is also authenticated in the same browser. The parent
    can always access /parent/ routes via require_parent, but on
    kid-facing pages the selected profile determines the viewer.
    """
    session = request.session

    profile_id = session.get("profile_id")
    if profile_id:
        profile = profile_repo.get(profile_id)
        if profile:
            # [] (no rows in profile_channels) = no restriction, see all.
            # Non-empty list = restricted to those channels only.
            allowed = profile.allowed_channel_ids or None
            return ViewerContext(
                viewer_type="child",
                profile_id=profile.id,
                profile_name=profile.name,
                display_name=profile.display_name,
                allowed_channel_ids=allowed,
                search_mode=profile.search_mode,
                theme=profile.theme,
                has_multiple_profiles=profile_repo.count() > 1,
            )

    if session.get("parent_authenticated"):
        return ViewerContext(viewer_type="parent")

    return ViewerContext(viewer_type="anonymous")


class NotAuthenticated(Exception):
    """Raised when a parent-only route is accessed without auth."""
    pass


class NotAChild(Exception):
    """Raised when a kid-only route is accessed by a non-child viewer."""
    pass


def require_parent(viewer: ViewerContext = Depends(get_viewer)) -> ViewerContext:
    """Dependency that ensures the viewer is an authenticated parent."""
    if not viewer.is_parent:
        raise NotAuthenticated()
    return viewer


def require_child(viewer: ViewerContext = Depends(get_viewer)) -> ViewerContext:
    """Dependency that ensures the viewer is an authenticated child.

    Kid-only routes (e.g. kid uploads) must redirect to /profiles when
    the viewer is anonymous or parent-only, rather than to the parent
    login. The global handler in app/main.py maps NotAChild to that.
    """
    if not viewer.is_child:
        raise NotAChild()
    return viewer
