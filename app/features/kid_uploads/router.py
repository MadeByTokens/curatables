"""Kid uploads — plain XHR multipart upload and kid-created channels.

Kids cannot use the tus.io resumable flow because tus-js-client 4.x
uses ES2015+ syntax that iOS 9 Safari cannot parse. This router
implements a single POST multipart endpoint that works on old
browsers via standard `<input type=file>` and FormData plus
XMLHttpRequest upload progress.
"""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from starlette.responses import HTMLResponse, RedirectResponse

from app.config import Config
from app.dependencies import (
    get_channel_service, get_config, get_event_service,
    get_storage_service, get_upload_service, require_child,
)
from app.models import ViewerContext
from app.services.channels import ChannelService
from app.services.events import EventService
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.uploads import UploadService, UploadError
from app.services.media_probe import UnsupportedCodec, ProbeError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kid-uploads"])


_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB copy buffer


def _effective_max(config: Config) -> int:
    """The smaller of the parent ceiling and the kid-specific ceiling."""
    return min(config.storage.max_upload_bytes, config.storage.max_kid_upload_bytes)


def _render_upload_page(request: Request, viewer: ViewerContext,
                        channels_svc: ChannelService, config: Config,
                        error: str | None = None,
                        selected_channel_id: int | None = None,
                        status_code: int = 200):
    channels = channels_svc.visible_to_kid(
        viewer.profile_id, viewer.allowed_channel_ids)
    if selected_channel_id is None and channels:
        selected_channel_id = channels[0].id
    return request.app.state.templates.TemplateResponse(request, "kid/upload.html", {
        "request": request,
        "viewer": viewer,
        "channels": channels,
        "selected_channel_id": selected_channel_id,
        "max_upload_bytes": _effective_max(config),
        "error": error,
    }, status_code=status_code)


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request,
                viewer: ViewerContext = Depends(require_child),
                channels_svc: ChannelService = Depends(get_channel_service),
                config: Config = Depends(get_config)):
    selected = request.query_params.get("selected")
    selected_id = None
    if selected and selected.isdigit():
        selected_id = int(selected)
    return _render_upload_page(request, viewer, channels_svc, config,
                               selected_channel_id=selected_id)


@router.post("/upload/new-channel")
def create_kid_channel(request: Request,
                       name: str = Form(...),
                       viewer: ViewerContext = Depends(require_child),
                       channels_svc: ChannelService = Depends(get_channel_service),
                       events: EventService = Depends(get_event_service),
                       config: Config = Depends(get_config)):
    try:
        channel = channels_svc.create_for_kid(name, owner_profile_id=viewer.profile_id)
    except ValueError as e:
        return _render_upload_page(request, viewer, channels_svc, config,
                                   error=str(e), status_code=400)

    events.log(
        "channel_created",
        profile_id=viewer.profile_id,
        data={"channel_id": channel.id, "name": channel.name},
    )
    return RedirectResponse(url=f"/upload?selected={channel.id}", status_code=302)


@router.post("/upload")
async def kid_upload(request: Request,
                     channel_id: int = Form(...),
                     title: str = Form(""),
                     file: UploadFile = File(...),
                     viewer: ViewerContext = Depends(require_child),
                     uploads: UploadService = Depends(get_upload_service),
                     channels_svc: ChannelService = Depends(get_channel_service),
                     storage: StorageService = Depends(get_storage_service),
                     events: EventService = Depends(get_event_service),
                     config: Config = Depends(get_config)):
    # 1. Channel must be in the kid's visible set. Guessing a sibling's
    #    private channel_id is not enough — it has to be reachable
    #    through list_visible_to.
    visible_channels = channels_svc.visible_to_kid(
        viewer.profile_id, viewer.allowed_channel_ids)
    visible_ids = {c.id for c in visible_channels}
    if channel_id not in visible_ids:
        logger.info("Kid %s tried to upload to channel %s outside their visible set",
                    viewer.profile_id, channel_id)
        return _render_upload_page(
            request, viewer, channels_svc, config,
            error="That channel isn't available. Pick one from the list.",
            status_code=403,
        )

    # 2. Measure the file size via the underlying SpooledTemporaryFile.
    #    UploadFile.seek only accepts (offset,) so use file.file directly.
    file.file.seek(0, 2)  # seek to end
    total_size = file.file.tell()
    file.file.seek(0)

    # 3. Enforce the kid-specific ceiling and the system quota.
    max_bytes = _effective_max(config)
    if total_size > max_bytes:
        return _render_upload_page(
            request, viewer, channels_svc, config,
            error=(f"This file is too big ({total_size // 1_048_576} MB). "
                   f"Maximum is {max_bytes // 1_048_576} MB."),
            selected_channel_id=channel_id,
            status_code=413,
        )

    allowed, reason = storage.check_can_write(required_bytes=total_size)
    if not allowed:
        logger.info("Kid upload refused for %s: %s", viewer.profile_id, reason)
        return _render_upload_page(
            request, viewer, channels_svc, config,
            error="There isn't enough free space on this computer. Ask a grown-up.",
            selected_channel_id=channel_id,
            status_code=507,
        )

    # 4. Stream into the tus staging area via the existing UploadService
    #    lifecycle (create -> append -> finalize). The kid route does
    #    not expose tus to the client; it just reuses the same server
    #    pipeline so validation, hashing, and dedup are identical.
    filename = file.filename or "upload.mp4"
    session = uploads.create(
        filename=filename,
        total_size=total_size,
        channel_id=channel_id,
        title=(title or "").strip(),
    )
    try:
        offset = 0
        while True:
            chunk = await file.read(_CHUNK_SIZE)
            if not chunk:
                break
            offset = uploads.append(session.token, offset, chunk)
    except Exception:
        uploads.cancel(session.token)
        raise

    # 5. Finalize. Kid-friendly error messages replace the technical
    #    ffmpeg conversion command — the full hint still lands in the
    #    server log for the parent to see.
    try:
        video = uploads.finalize(session.token)
    except UnsupportedCodec as e:
        logger.info("Kid %s upload rejected: unsupported codec '%s'. Hint: %s",
                    viewer.profile_id, e.codec_name, e.conversion_hint)
        return _render_upload_page(
            request, viewer, channels_svc, config,
            error="This video is in a format we can't read. Ask a grown-up for help.",
            selected_channel_id=channel_id,
            status_code=415,
        )
    except ProbeError as e:
        logger.info("Kid %s upload rejected: probe failure: %s", viewer.profile_id, e)
        return _render_upload_page(
            request, viewer, channels_svc, config,
            error="We couldn't read this video file. Try a different one.",
            selected_channel_id=channel_id,
            status_code=400,
        )
    except InsufficientDiskSpace as e:
        return _render_upload_page(
            request, viewer, channels_svc, config,
            error="There isn't enough free space on this computer. Ask a grown-up.",
            selected_channel_id=channel_id,
            status_code=507,
        )
    except UploadError as e:
        return _render_upload_page(
            request, viewer, channels_svc, config,
            error="Upload failed. Try again.",
            selected_channel_id=channel_id,
            status_code=400,
        )

    events.log(
        "video_uploaded_by_kid",
        video_id=video.video_id,
        profile_id=viewer.profile_id,
        data={"title": video.title, "channel_id": channel_id},
    )
    return RedirectResponse(url=f"/watch/{video.video_id}", status_code=302)
