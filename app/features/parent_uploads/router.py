"""Parent uploads — tus.io 1.0.0 Core Protocol server for resumable uploads.

Implements the minimum of the tus.io protocol needed for tus-js-client:
  OPTIONS (capabilities), POST (create), HEAD (offset), PATCH (append),
  DELETE (terminate). A separate POST /finalize endpoint runs ffprobe
  validation, content-hashing, and videos-row insertion once the bytes
  are fully uploaded.
"""

from __future__ import annotations

import base64
import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_channel_repo, get_config, get_storage_service,
    get_upload_service, require_parent,
)
from app.config import Config
from app.models import ViewerContext
from app.repositories import ChannelRepository
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.uploads import (
    UploadService, UploadNotFound, UploadOffsetMismatch, UploadError,
)
from app.services.media_probe import UnsupportedCodec, ProbeError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/parent", tags=["parent-uploads"])


_TUS_RESUMABLE = "1.0.0"
_TUS_VERSION = "1.0.0"
_TUS_EXTENSION = "creation,termination"
_DEFAULT_FAMILY_CHANNEL = "Family"


def _tus_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    headers = {
        "Tus-Resumable": _TUS_RESUMABLE,
        "Cache-Control": "no-store",
    }
    if extra:
        headers.update(extra)
    return headers


def _parse_upload_metadata(raw: str) -> dict[str, str]:
    """Decode the tus Upload-Metadata header into {key: value}.

    Format per tus spec: comma-separated `key base64value` pairs. A
    key with no value is allowed but we don't use that form here.
    """
    out: dict[str, str] = {}
    if not raw:
        return out
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        parts = pair.split(" ", 1)
        key = parts[0]
        if len(parts) == 2:
            try:
                value = base64.b64decode(parts[1]).decode("utf-8", "replace")
            except Exception:
                value = ""
        else:
            value = ""
        out[key] = value
    return out


def _ensure_default_channel(channel_repo: ChannelRepository) -> int:
    """Return the id of a default upload target channel.

    Auto-creates "Family" on first call if no channels exist, so the
    parent always has something to upload into.
    """
    channels = channel_repo.list()
    if not channels:
        return channel_repo.create(_DEFAULT_FAMILY_CHANNEL)
    for c in channels:
        if c.name == _DEFAULT_FAMILY_CHANNEL:
            return c.id
    return channels[0].id


# ---------------------------------------------------------------------------
# Upload page
# ---------------------------------------------------------------------------

@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request,
                viewer: ViewerContext = Depends(require_parent),
                channel_repo: ChannelRepository = Depends(get_channel_repo),
                config: Config = Depends(get_config)):
    default_channel_id = _ensure_default_channel(channel_repo)
    channels = channel_repo.list()
    return request.app.state.templates.TemplateResponse(request, "parent/upload.html", {
        "request": request,
        "channels": channels,
        "default_channel_id": default_channel_id,
        "max_upload_bytes": config.storage.max_upload_bytes,
        "error": None,
    })


# ---------------------------------------------------------------------------
# tus protocol
# ---------------------------------------------------------------------------

@router.options("/upload/tus")
def tus_options(request: Request,
                config: Config = Depends(get_config)):
    return Response(status_code=204, headers=_tus_headers({
        "Tus-Version": _TUS_VERSION,
        "Tus-Extension": _TUS_EXTENSION,
        "Tus-Max-Size": str(config.storage.max_upload_bytes),
    }))


@router.options("/upload/tus/{token}")
def tus_options_token(token: str,
                      config: Config = Depends(get_config)):
    return Response(status_code=204, headers=_tus_headers({
        "Tus-Version": _TUS_VERSION,
        "Tus-Extension": _TUS_EXTENSION,
        "Tus-Max-Size": str(config.storage.max_upload_bytes),
    }))


@router.post("/upload/tus")
async def tus_create(request: Request,
                     viewer: ViewerContext = Depends(require_parent),
                     uploads: UploadService = Depends(get_upload_service),
                     storage: StorageService = Depends(get_storage_service),
                     config: Config = Depends(get_config)):
    headers = request.headers
    if headers.get("tus-resumable") != _TUS_RESUMABLE:
        return Response(status_code=412, headers=_tus_headers(),
                        content="Unsupported Tus-Resumable version",
                        media_type="text/plain")

    try:
        upload_length = int(headers.get("upload-length", "-1"))
    except ValueError:
        return Response(status_code=400, headers=_tus_headers(),
                        content="Invalid Upload-Length",
                        media_type="text/plain")
    if upload_length <= 0:
        return Response(status_code=400, headers=_tus_headers(),
                        content="Upload-Length required and positive",
                        media_type="text/plain")

    if upload_length > config.storage.max_upload_bytes:
        return Response(status_code=413, headers=_tus_headers(),
                        content=(f"Upload exceeds configured max of "
                                 f"{config.storage.max_upload_bytes} bytes"),
                        media_type="text/plain")

    allowed, reason = storage.check_can_write(required_bytes=upload_length)
    if not allowed:
        return Response(status_code=507, headers=_tus_headers(),
                        content=reason, media_type="text/plain")

    metadata = _parse_upload_metadata(headers.get("upload-metadata", ""))
    filename = metadata.get("filename", "upload.mp4")
    title = metadata.get("title", "")
    try:
        channel_id = int(metadata.get("channel_id", "0"))
    except ValueError:
        channel_id = 0
    if channel_id <= 0:
        return Response(status_code=400, headers=_tus_headers(),
                        content="channel_id metadata required",
                        media_type="text/plain")

    session = uploads.create(
        filename=filename,
        total_size=upload_length,
        channel_id=channel_id,
        title=title,
    )
    return Response(status_code=201, headers=_tus_headers({
        "Location": f"/parent/upload/tus/{session.token}",
        "Upload-Offset": "0",
    }))


@router.head("/upload/tus/{token}")
def tus_head(token: str,
             viewer: ViewerContext = Depends(require_parent),
             uploads: UploadService = Depends(get_upload_service)):
    try:
        session = uploads.load_session(token)
        offset = uploads.get_offset(token)
    except UploadNotFound:
        return Response(status_code=404, headers=_tus_headers())
    return Response(status_code=200, headers=_tus_headers({
        "Upload-Offset": str(offset),
        "Upload-Length": str(session.total_size),
    }))


@router.patch("/upload/tus/{token}")
async def tus_patch(request: Request, token: str,
                    viewer: ViewerContext = Depends(require_parent),
                    uploads: UploadService = Depends(get_upload_service),
                    storage: StorageService = Depends(get_storage_service)):
    headers = request.headers
    if headers.get("tus-resumable") != _TUS_RESUMABLE:
        return Response(status_code=412, headers=_tus_headers())
    if headers.get("content-type", "") != "application/offset+octet-stream":
        return Response(status_code=415, headers=_tus_headers(),
                        content="Content-Type must be application/offset+octet-stream",
                        media_type="text/plain")
    try:
        offset = int(headers.get("upload-offset", "-1"))
    except ValueError:
        return Response(status_code=400, headers=_tus_headers())
    if offset < 0:
        return Response(status_code=400, headers=_tus_headers())

    allowed, reason = storage.check_can_write()
    if not allowed:
        return Response(status_code=507, headers=_tus_headers(),
                        content=reason, media_type="text/plain")

    chunk = await request.body()
    try:
        new_offset = uploads.append(token, offset, chunk)
    except UploadNotFound:
        return Response(status_code=404, headers=_tus_headers())
    except UploadOffsetMismatch as e:
        return Response(status_code=409, headers=_tus_headers(),
                        content=str(e), media_type="text/plain")

    return Response(status_code=204, headers=_tus_headers({
        "Upload-Offset": str(new_offset),
    }))


@router.delete("/upload/tus/{token}")
def tus_delete(token: str,
               viewer: ViewerContext = Depends(require_parent),
               uploads: UploadService = Depends(get_upload_service)):
    uploads.cancel(token)
    return Response(status_code=204, headers=_tus_headers())


# ---------------------------------------------------------------------------
# Finalize
# ---------------------------------------------------------------------------

@router.post("/upload/finalize/{token}")
def upload_finalize(request: Request, token: str,
                    viewer: ViewerContext = Depends(require_parent),
                    uploads: UploadService = Depends(get_upload_service),
                    channel_repo: ChannelRepository = Depends(get_channel_repo),
                    config: Config = Depends(get_config)):
    templates = request.app.state.templates
    try:
        video = uploads.finalize(token)
    except UnsupportedCodec as e:
        logger.info("Upload %s rejected: unsupported codec %s", token, e.codec_name)
        return templates.TemplateResponse(request, "parent/upload.html", {
            "request": request,
            "channels": channel_repo.list(),
            "default_channel_id": _ensure_default_channel(channel_repo),
            "max_upload_bytes": config.storage.max_upload_bytes,
            "error": e.conversion_hint,
        }, status_code=415)
    except ProbeError as e:
        logger.info("Upload %s rejected: probe failure: %s", token, e)
        return templates.TemplateResponse(request, "parent/upload.html", {
            "request": request,
            "channels": channel_repo.list(),
            "default_channel_id": _ensure_default_channel(channel_repo),
            "max_upload_bytes": config.storage.max_upload_bytes,
            "error": f"Could not read the uploaded file: {e}",
        }, status_code=400)
    except InsufficientDiskSpace as e:
        return templates.TemplateResponse(request, "parent/upload.html", {
            "request": request,
            "channels": channel_repo.list(),
            "default_channel_id": _ensure_default_channel(channel_repo),
            "max_upload_bytes": config.storage.max_upload_bytes,
            "error": str(e),
        }, status_code=507)
    except (UploadNotFound, UploadError) as e:
        return templates.TemplateResponse(request, "parent/upload.html", {
            "request": request,
            "channels": channel_repo.list(),
            "default_channel_id": _ensure_default_channel(channel_repo),
            "max_upload_bytes": config.storage.max_upload_bytes,
            "error": str(e),
        }, status_code=400)

    return RedirectResponse(
        url=f"/parent/content?highlight={quote(video.video_id)}",
        status_code=302,
    )
