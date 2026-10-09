"""Kid uploads — plain XHR multipart upload and kid-created channels.

Kids cannot use the tus.io resumable flow because tus-js-client 4.x
uses ES2015+ syntax that iOS 9 Safari cannot parse. This router
implements a single POST multipart endpoint that works on old
browsers via standard `<input type=file>` and FormData plus
XMLHttpRequest upload progress.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.config import Config
from app.dependencies import (
    get_channel_service, get_config, get_event_service,
    get_storage_service, get_upload_service, require_kid_feature,
)
from app.web import is_xhr
from app.models import ViewerContext
from app.template_utils import render_kid_template
from app.services.channels import ChannelService
from app.services.events import EventService
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.uploads import DuplicateUpload, UploadService, UploadError
from app.services.media_probe import UnsupportedCodec, ProbeError

logger = logging.getLogger(__name__)

router = APIRouter(tags=["kid-uploads"])

_uploads = require_kid_feature("allow_upload")


_CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB copy buffer


def _effective_max(config: Config) -> int:
    """The smaller of the parent ceiling and the kid-specific ceiling."""
    return min(config.storage.max_upload_bytes, config.storage.max_kid_upload_bytes)


def _render_upload_page(request: Request, viewer: ViewerContext,
                        channels_svc: ChannelService, config: Config,
                        error: str | None = None,
                        selected_channel_id: int | None = None,
                        status_code: int = 200,
                        sent_for_review: bool = False):
    channels = channels_svc.visible_to_kid(viewer.kid_id)
    if selected_channel_id is None and channels:
        selected_channel_id = channels[0].id
    response = render_kid_template(request, viewer, "kid/upload.html", {
        "channels": channels,
        "selected_channel_id": selected_channel_id,
        "max_upload_bytes": _effective_max(config),
        "error": error,
        "sent_for_review": sent_for_review,
    })
    response.status_code = status_code
    return response


@router.get("/upload", response_class=HTMLResponse)
def upload_page(request: Request,
                viewer: ViewerContext = Depends(_uploads),
                channels_svc: ChannelService = Depends(get_channel_service),
                config: Config = Depends(get_config)):
    selected = request.query_params.get("selected")
    selected_id = None
    if selected and selected.isdigit():
        selected_id = int(selected)
    return _render_upload_page(request, viewer, channels_svc, config,
                               selected_channel_id=selected_id,
                               sent_for_review=request.query_params.get("sent") == "1")


@router.post("/upload/new-channel")
def create_kid_channel(request: Request,
                       name: str = Form(...),
                       viewer: ViewerContext = Depends(_uploads),
                       channels_svc: ChannelService = Depends(get_channel_service),
                       events: EventService = Depends(get_event_service),
                       config: Config = Depends(get_config)):
    try:
        channel = channels_svc.create_for_kid(name, owner_profile_id=viewer.kid_id)
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
                     viewer: ViewerContext = Depends(_uploads),
                     uploads: UploadService = Depends(get_upload_service),
                     channels_svc: ChannelService = Depends(get_channel_service),
                     storage: StorageService = Depends(get_storage_service),
                     events: EventService = Depends(get_event_service),
                     config: Config = Depends(get_config)):
    # The upload page posts with XHR (for the progress bar) and gets
    # JSON back; a plain form post (no JS) gets the page / a redirect.
    xhr = is_xhr(request)

    def fail(message: str, status_code: int):
        if xhr:
            return JSONResponse({"error": message}, status_code=status_code)
        return _render_upload_page(
            request, viewer, channels_svc, config, error=message,
            selected_channel_id=channel_id, status_code=status_code)

    def done(url: str):
        if xhr:
            return JSONResponse({"redirect": url})
        return RedirectResponse(url=url, status_code=302)

    # 1. Channel must be in the kid's visible set. Guessing a sibling's
    #    private channel_id is not enough — it has to be reachable
    #    through list_visible_to.
    visible_channels = channels_svc.visible_to_kid(viewer.kid_id)
    visible_by_id = {c.id: c for c in visible_channels}
    if channel_id not in visible_by_id:
        logger.info("Kid %s tried to upload to channel %s outside their visible set",
                    viewer.profile_id, channel_id)
        return fail("That channel isn't available. Pick one from the list.", 403)

    # 2. Measure the file size via the underlying SpooledTemporaryFile.
    #    UploadFile.seek only accepts (offset,) so use file.file directly.
    file.file.seek(0, 2)  # seek to end
    total_size = file.file.tell()
    file.file.seek(0)

    # 3. Enforce the kid-specific ceiling and the system quota.
    max_bytes = _effective_max(config)
    if total_size > max_bytes:
        return fail("This video is too big. Ask a grown-up to help.", 413)

    allowed, reason = storage.check_can_write(required_bytes=total_size)
    if not allowed:
        logger.info("Kid upload refused for %s: %s", viewer.profile_id, reason)
        return fail("There isn't enough free space on this computer. Ask a grown-up.", 507)

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
    owns_channel = visible_by_id[channel_id].owner_profile_id == viewer.profile_id
    try:
        video = await run_in_threadpool(
            uploads.finalize, session.token,
            status="active" if owns_channel else "review")
    except UnsupportedCodec as e:
        logger.info("Kid %s upload rejected: unsupported codec '%s'. Hint: %s",
                    viewer.profile_id, e.codec_name, e.conversion_hint)
        return fail("This video is in a format we can't read. Ask a grown-up for help.", 415)
    except ProbeError as e:
        logger.info("Kid %s upload rejected: probe failure: %s", viewer.profile_id, e)
        return fail("We couldn't read this video file. Try a different one.", 400)
    except InsufficientDiskSpace:
        return fail("There isn't enough free space on this computer. Ask a grown-up.", 507)
    except DuplicateUpload as e:
        dup = e.video
        if (dup.channel_id in visible_by_id and dup.status == "active"
                and dup.download_status == "ready"):
            return done(f"/watch/{dup.video_id}")
        return fail("This video is already here. Ask a grown-up to find it.", 409)
    except UploadError:
        return fail("Upload failed. Try again.", 400)

    events.log(
        "video_uploaded_by_kid",
        video_id=video.video_id,
        profile_id=viewer.profile_id,
        data={"title": video.title, "channel_id": channel_id},
    )
    return done("/upload?sent=1" if video.status != "active"
                else f"/watch/{video.video_id}")
