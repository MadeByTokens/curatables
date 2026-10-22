"""Media streaming: video and thumbnail serving."""

import logging
import os
import re
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import FileResponse, Response

from app.dependencies import (
    get_config, get_content_service, get_kid_library_service, get_storage_service,
    get_thumbnail_service, get_viewer,
)
from app.services.content import ContentService, VideoNotFound
from app.services.storage import StorageService
from app.services.subtitles import language_code, language_label, to_vtt
from app.services.thumbnails import ThumbnailService
from app.services.kid_library import KidLibraryService
from app.config import Config
from app.models import ViewerContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/media", tags=["media"])

_PLACEHOLDER_THUMB = Path(__file__).resolve().parents[2] / "static" / "placeholder-thumb.svg"

_TRANSPARENT_PIXEL = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _stat(path):
    """The file's stat, or None when it isn't there. Taken once and handed
    to FileResponse, as the file can go (its video deleted) at any time."""
    try:
        return os.stat(path)
    except OSError:
        return None


def _sniff_image_mime(path) -> str:
    # A thumbnail file may hold WebP, PNG or GIF bytes whatever its name.
    # `<img>` sniffs, but `<video poster>` honors the declared type, so
    # return the real one.
    try:
        with open(path, "rb") as f:
            head = f.read(12)
    except OSError:
        return "image/jpeg"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    return "image/jpeg"


@router.get("/thumb/{video_id}")
def serve_thumbnail(request: Request, video_id: str,
                    viewer: ViewerContext = Depends(get_viewer),
                    thumbnails: ThumbnailService = Depends(get_thumbnail_service),
                    content: ContentService = Depends(get_content_service),
                    kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    # Parents can see all thumbnails (needed for preview before adding).
    # Kids can only see thumbnails for videos they're allowed to access.
    if not viewer.is_parent:
        try:
            video = content.get_video(video_id)
        except VideoNotFound:
            return Response(content=_TRANSPARENT_PIXEL, media_type="image/png")
        if video.status != "active" or not content.can_view(video, viewer):
            return Response(content=_TRANSPARENT_PIXEL, media_type="image/png")

    # The thumbnail URL is stable, but the bytes behind it change when a
    # kid uploads a custom thumbnail (or when the canonical cache is
    # rebuilt). Cache-Control: no-cache forces the browser to revalidate
    # every request so it sees updates immediately; ETag handles the
    # efficient 304 path when nothing actually changed.
    no_cache = {"Cache-Control": "no-cache, max-age=0, must-revalidate"}

    # Per-kid custom thumbnail override
    if viewer.is_child and viewer.profile_id:
        custom = kid_lib.get_custom_thumb_path(viewer.profile_id, video_id)
        stat = _stat(custom) if custom else None
        if custom and stat:
            return FileResponse(str(custom), stat_result=stat,
                                media_type=_sniff_image_mime(custom),
                                headers=no_cache)

    if not thumbnails.is_cached(video_id):
        try:
            video = content.get_video(video_id)
            thumbnails.ensure_thumbnail(video_id, video.thumbnail_url)
        except Exception as e:
            logger.debug("Thumbnail fetch failed for %s: %s", video_id, e)

    path = thumbnails.get_path(video_id)
    stat = _stat(path)
    if stat:
        return FileResponse(str(path), stat_result=stat,
                            media_type=_sniff_image_mime(path),
                            headers=no_cache)

    return FileResponse(str(_PLACEHOLDER_THUMB), media_type="image/svg+xml")


_VIDEO_MIME_BY_EXT = {
    ".mp4": "video/mp4",
    ".m4v": "video/mp4",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mpg": "video/mpeg",
    ".mpeg": "video/mpeg",
}


def _download_name(title: str, suffix: str) -> str:
    """A file name from the title: no path separators, quotes or control
    characters, and short enough for any file system."""
    name = re.sub(r'[\x00-\x1f\x7f/\\:*?"<>|]+', " ", title)
    name = " ".join(name.split())[:100].strip(" .") or "video"
    return name + suffix


@router.get("/video/{video_id}")
def serve_video(request: Request, video_id: str,
                download: bool = Query(False),
                viewer: ViewerContext = Depends(get_viewer),
                content: ContentService = Depends(get_content_service),
                storage: StorageService = Depends(get_storage_service),
                kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    """The video file. With ?download=1 the browser saves it (on an iPad,
    into Files) under the title the kid sees."""
    try:
        if viewer.is_parent:
            video = content.get_video(video_id)
        else:
            video = content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return Response(status_code=404, content="Video not found")
    if download and viewer.is_child and not viewer.allow_download:
        return Response(status_code=403, content="Downloads are off for this profile")

    video_path = storage.video_file_path(video)
    try:
        # Once, here: the file can go (a parent deleting the video) while
        # a player is still asking for pieces of it.
        stat = video_path.stat()
    except OSError:
        return Response(status_code=404, content="Video not downloaded yet")

    media_type = _VIDEO_MIME_BY_EXT.get(video_path.suffix.lower(), "video/mp4")
    if not download:
        return FileResponse(str(video_path), media_type=media_type, stat_result=stat)
    if viewer.is_child and viewer.profile_id:
        video = kid_lib.apply_overrides(viewer.profile_id, [video])[0]
    return FileResponse(str(video_path), media_type=media_type, stat_result=stat,
                        filename=_download_name(video.title, video_path.suffix.lower()))


@router.get("/subs/{video_id}")
def list_subtitles(request: Request, video_id: str,
                   viewer: ViewerContext = Depends(get_viewer),
                   content: ContentService = Depends(get_content_service),
                   storage: StorageService = Depends(get_storage_service)):
    """Return available subtitle tracks as JSON."""
    try:
        video = content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return Response(status_code=404)
    return [{"lang": language_code(s["lang"]), "label": language_label(s["lang"]),
             "url": f"/media/subs/{video_id}/{s['filename']}"}
            for s in storage.list_subtitles(video)]


@router.get("/subs/{video_id}/{filename}")
def serve_subtitle(request: Request, video_id: str, filename: str,
                   viewer: ViewerContext = Depends(get_viewer),
                   content: ContentService = Depends(get_content_service),
                   storage: StorageService = Depends(get_storage_service)):
    """Serve a subtitle file as WebVTT, the only format <track> plays.

    SubRip files are converted and VTT cue settings are stripped on the
    fly, so captions display centered regardless of whatever alignment
    metadata came with the source.
    """
    try:
        video = content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return Response(status_code=404)
    # .name drops any directory part: no path traversal.
    sub_path = storage.subtitle_dir(video) / Path(filename).name
    if not sub_path.is_file() or sub_path.suffix not in (".vtt", ".srt"):
        return Response(status_code=404)
    try:
        raw = sub_path.read_bytes()
    except FileNotFoundError:
        return Response(status_code=404)
    except OSError:
        return Response(status_code=500, content="Failed to read subtitle")
    return Response(content=to_vtt(raw, sub_path.suffix),
                    media_type="text/vtt; charset=utf-8")


def _serve_channel_asset(config: Config, content: ContentService, viewer: ViewerContext,
                         channel_id: int, asset_prefix: str):
    """Serve a channel banner or icon from {media_dir}/channels/{id}/{prefix}.*"""
    visible = content.visible_channel_ids(viewer)
    if visible is not None and channel_id not in visible:
        return Response(status_code=404)
    channel_dir = config.media_dir / "channels" / str(channel_id)
    if channel_dir.exists():
        for f in channel_dir.glob(f"{asset_prefix}.*"):
            return FileResponse(str(f), media_type=_sniff_image_mime(f))
    return Response(status_code=404)


_KID_PICTURE = re.compile(r"^kid-\d+\.[a-z]+$")


@router.get("/channel/{channel_id}/my-icon")
def serve_kid_channel_picture(channel_id: int,
                              viewer: ViewerContext = Depends(get_viewer),
                              content: ContentService = Depends(get_content_service),
                              config: Config = Depends(get_config)):
    """The picture this kid gave a channel; nobody else's."""
    if not viewer.is_child or not viewer.profile_id or content.channel_for_viewer(channel_id, viewer) is None:
        return Response(status_code=404)
    picture = content.channel_repo.look(viewer.profile_id, channel_id)["picture"]
    if not _KID_PICTURE.match(picture):
        return Response(status_code=404)
    path = config.media_dir / "channels" / str(channel_id) / picture
    if not path.is_file():
        return Response(status_code=404)
    return FileResponse(str(path), media_type=_sniff_image_mime(path))


@router.get("/channel/{channel_id}/banner")
def serve_channel_banner(channel_id: int,
                         viewer: ViewerContext = Depends(get_viewer),
                         content: ContentService = Depends(get_content_service),
                         config: Config = Depends(get_config)):
    return _serve_channel_asset(config, content, viewer, channel_id, "banner")


@router.get("/channel/{channel_id}/icon")
def serve_channel_icon(channel_id: int,
                       viewer: ViewerContext = Depends(get_viewer),
                       content: ContentService = Depends(get_content_service),
                       config: Config = Depends(get_config)):
    return _serve_channel_asset(config, content, viewer, channel_id, "icon")
