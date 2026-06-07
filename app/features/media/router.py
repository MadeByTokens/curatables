"""Media streaming: video and thumbnail serving."""

import logging
import re

from fastapi import APIRouter, Depends, Request
from starlette.responses import FileResponse, Response

logger = logging.getLogger(__name__)

from app.dependencies import get_viewer, get_content_service, get_storage_service, get_thumbnail_service, get_kid_library_service, get_config
from app.services.content import ContentService, VideoNotFound
from app.services.storage import StorageService
from app.services.thumbnails import ThumbnailService
from app.services.kid_library import KidLibraryService
from app.config import Config
from app.models import ViewerContext

router = APIRouter(prefix="/media", tags=["media"])


_TRANSPARENT_PIXEL = (
    b'\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01'
    b'\x00\x00\x00\x01\x08\x06\x00\x00\x00\x1f\x15\xc4\x89'
    b'\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01'
    b'\r\n\xb4\x00\x00\x00\x00IEND\xaeB`\x82'
)


def _sniff_image_mime(path) -> str:
    # The cache filename is always `thumb.jpg`, but yt-dlp / YouTube may
    # hand us WebP, PNG, or GIF bytes. `<img>` sniffs, but `<video poster>`
    # honors the declared type — so we must return the real one.
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
            content.get_video_for_viewer(video_id, viewer)
        except VideoNotFound:
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
        if custom and custom.exists():
            return FileResponse(str(custom),
                                media_type=_sniff_image_mime(custom),
                                headers=no_cache)

    if not thumbnails.is_cached(video_id):
        try:
            video = content.get_video(video_id)
            thumbnails.ensure_thumbnail(video_id, video.thumbnail_url)
        except Exception as e:
            logger.debug("Thumbnail fetch failed for %s: %s", video_id, e)

    path = thumbnails.get_path(video_id)
    if path.exists():
        return FileResponse(str(path),
                            media_type=_sniff_image_mime(path),
                            headers=no_cache)

    # Serve "thumbnail not available" placeholder
    import os
    placeholder = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "static", "placeholder-thumb.svg")
    return FileResponse(placeholder, media_type="image/svg+xml")


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


@router.get("/video/{video_id}")
def serve_video(request: Request, video_id: str,
                viewer: ViewerContext = Depends(get_viewer),
                content: ContentService = Depends(get_content_service),
                storage: StorageService = Depends(get_storage_service)):
    try:
        video = content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return Response(status_code=404, content="Video not found")

    video_path = storage.video_file_path(video)
    if not video_path.exists():
        return Response(status_code=404, content="Video not downloaded yet")

    media_type = _VIDEO_MIME_BY_EXT.get(video_path.suffix.lower(), "video/mp4")
    return FileResponse(str(video_path), media_type=media_type)


@router.get("/subs/{video_id}")
def list_subtitles(request: Request, video_id: str,
                   storage: StorageService = Depends(get_storage_service)):
    """Return available subtitle tracks as JSON."""
    subs = storage.list_subtitles(video_id)
    return [{"lang": s["lang"], "url": f"/media/subs/{video_id}/{s['filename']}"} for s in subs]


_VTT_CUE_TIMING_RE = re.compile(
    r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})"
)


def _normalize_vtt(raw: str) -> str:
    """Strip cue settings (position / align / line / size) from a VTT.

    YouTube auto-captions ship with `align:start position:0%` on every
    cue, which anchors the text at the left edge of the video and
    causes native HTML5 captions to render off-center. Removing the
    settings lets the browser fall back to the centered default.
    """
    out = []
    for line in raw.splitlines():
        m = _VTT_CUE_TIMING_RE.match(line)
        if m:
            out.append(f"{m.group(1)} --> {m.group(2)}")
        else:
            out.append(line)
    return "\n".join(out)


@router.get("/subs/{video_id}/{filename}")
def serve_subtitle(request: Request, video_id: str, filename: str,
                   storage: StorageService = Depends(get_storage_service)):
    """Serve a subtitle file.

    For VTT files, cue settings are stripped on the fly so captions
    display centered regardless of whatever alignment metadata
    yt-dlp downloaded from the source.
    """
    from pathlib import Path
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    sub_path = storage.video_dir(video_id) / safe_name
    if not sub_path.exists() or sub_path.suffix not in (".vtt", ".srt"):
        return Response(status_code=404)

    if sub_path.suffix == ".vtt":
        try:
            raw = sub_path.read_text(encoding="utf-8")
        except OSError:
            return Response(status_code=500, content="Failed to read subtitle")
        normalized = _normalize_vtt(raw)
        return Response(content=normalized, media_type="text/vtt; charset=utf-8")

    return FileResponse(str(sub_path), media_type="application/x-subrip")


def _serve_channel_asset(config, channel_id: int, asset_prefix: str):
    """Serve a channel banner or icon from {data_dir}/channels/{id}/{prefix}.*"""
    channel_dir = config.data_dir / "channels" / str(channel_id)
    if channel_dir.exists():
        for f in channel_dir.glob(f"{asset_prefix}.*"):
            return FileResponse(str(f), media_type=_sniff_image_mime(f))
    return Response(status_code=404)


@router.get("/channel/{channel_id}/banner")
def serve_channel_banner(channel_id: int,
                         config: Config = Depends(get_config)):
    return _serve_channel_asset(config, channel_id, "banner")


@router.get("/channel/{channel_id}/icon")
def serve_channel_icon(channel_id: int,
                       config: Config = Depends(get_config)):
    return _serve_channel_asset(config, channel_id, "icon")
