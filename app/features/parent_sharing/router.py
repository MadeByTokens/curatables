from __future__ import annotations
"""Shared-curation routes: export a channel / import a .ytc or .txt file.

Export hangs off /parent/channels/{id}/export?format=ytc|txt|pdf;
import gets its own /parent/channels/import form. After a successful
import POST we render the existing /parent/add preview template in
place (same URL), so the parent's review flow is identical whether
they added videos by URL or via import.
"""

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    require_parent, get_channel_service, get_content_service,
)
from app.services.channels import ChannelService
from app.services.content import ContentService
from app.services.sharing import (
    encode_ytc_bytes, render_text, render_pdf,
    decode_ytc, parse_text,
    pdf_available, SharingError, SharingUnavailable,
)
from app.models import ViewerContext

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/parent/channels", tags=["parent-sharing"])

# Max bytes accepted on import — defensive cap on top of the body-size
# middleware. A hand-curated .ytc with ~1000 videos is well under 1 MB.
_IMPORT_MAX_BYTES = 5 * 1024 * 1024

_VALID_FORMATS = {"ytc", "txt", "pdf"}


def _safe_filename(name: str) -> str:
    """Collapse anything that isn't alnum/dash/underscore to '-' so we
    can't emit a Content-Disposition with a path traversal in it."""
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "-" for c in name)
    cleaned = "-".join(cleaned.split())  # squash whitespace
    return cleaned.strip("-") or "channel"


@router.get("/{channel_id}/export")
def export_channel(request: Request, channel_id: int,
                   format: str = "ytc",
                   viewer: ViewerContext = Depends(require_parent),
                   channels_svc: ChannelService = Depends(get_channel_service),
                   content: ContentService = Depends(get_content_service)):
    fmt = (format or "").lower()
    if fmt not in _VALID_FORMATS:
        return Response(
            f"Unknown format {format!r}. Use one of: {', '.join(sorted(_VALID_FORMATS))}.",
            status_code=400, media_type="text/plain")
    channel = channels_svc.get(channel_id)
    if channel is None:
        return RedirectResponse(url="/parent/channels/", status_code=302)
    # All videos in the channel regardless of status — the recipient
    # decides what to do. Hidden videos are still curated content.
    videos = content.video_repo.list(
        status=None, channel_id=channel_id, limit=10_000, offset=0)

    stem = _safe_filename(channel.name)
    if fmt == "ytc":
        body = encode_ytc_bytes(channel, videos)
        return Response(
            content=body,
            media_type="application/json",
            headers={"Content-Disposition": f'attachment; filename="{stem}.ytc"'},
        )
    if fmt == "txt":
        body = render_text(channel, videos).encode("utf-8")
        return Response(
            content=body,
            media_type="text/plain; charset=utf-8",
            headers={"Content-Disposition": f'attachment; filename="{stem}.txt"'},
        )
    # fmt == "pdf"
    if not pdf_available():
        return request.app.state.templates.TemplateResponse(request,
            "parent/error.html", {
                "request": request,
                "heading": "PDF export not available",
                "detail": (
                    "The reportlab library is not installed on this server. "
                    "Install it to enable PDF export: "
                    "pip install 'reportlab>=4.0,<5.0'"
                ),
            }, status_code=503)
    try:
        body = render_pdf(channel, videos)
    except SharingUnavailable as e:
        return request.app.state.templates.TemplateResponse(request,
            "parent/error.html", {
                "request": request,
                "heading": "PDF export failed",
                "detail": str(e),
            }, status_code=503)
    return Response(
        content=body,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{stem}.pdf"'},
    )


@router.get("/import", response_class=HTMLResponse)
def import_form(request: Request,
                viewer: ViewerContext = Depends(require_parent),
                channels_svc: ChannelService = Depends(get_channel_service)):
    return request.app.state.templates.TemplateResponse(request,
        "parent/sharing_import.html", {
            "request": request,
            "channels": channels_svc.list(),
            "error": None,
            "max_bytes": _IMPORT_MAX_BYTES,
        })


@router.post("/import")
async def import_submit(request: Request,
                        file: UploadFile = File(None),
                        pasted: str = Form(""),
                        target_channel_id: str = Form(""),
                        new_channel_name: str = Form(""),
                        viewer: ViewerContext = Depends(require_parent),
                        channels_svc: ChannelService = Depends(get_channel_service),
                        content: ContentService = Depends(get_content_service)):
    templates = request.app.state.templates

    # Pick up the raw bytes from either the uploaded file or the
    # paste-in textarea. File wins if both are present.
    raw: bytes = b""
    filename = ""
    if file is not None and file.filename:
        raw = await file.read()
        filename = file.filename
    elif pasted.strip():
        raw = pasted.encode("utf-8")
        filename = "pasted.txt"

    if not raw:
        return templates.TemplateResponse(request, "parent/sharing_import.html", {
            "request": request,
            "channels": channels_svc.list(),
            "error": "Pick a file or paste a URL list first.",
            "max_bytes": _IMPORT_MAX_BYTES,
        }, status_code=400)

    if len(raw) > _IMPORT_MAX_BYTES:
        return templates.TemplateResponse(request, "parent/sharing_import.html", {
            "request": request,
            "channels": channels_svc.list(),
            "error": (
                f"File is too large ({len(raw)/1024/1024:.1f} MB). "
                f"Maximum is {_IMPORT_MAX_BYTES/1024/1024:.0f} MB."),
            "max_bytes": _IMPORT_MAX_BYTES,
        }, status_code=413)

    # Format dispatch: .ytc vs plain text. File extension is the
    # primary signal; fall back to "starts with {" for JSON autodetect.
    lower = (filename or "").lower()
    try:
        if lower.endswith(".ytc") or raw.lstrip().startswith(b"{"):
            payload = decode_ytc(raw)
        else:
            payload = parse_text(raw)
    except SharingError as e:
        return templates.TemplateResponse(request, "parent/sharing_import.html", {
            "request": request,
            "channels": channels_svc.list(),
            "error": str(e),
            "max_bytes": _IMPORT_MAX_BYTES,
        }, status_code=400)

    if not payload.entries:
        return templates.TemplateResponse(request, "parent/sharing_import.html", {
            "request": request,
            "channels": channels_svc.list(),
            "error": "No URLs found in this file.",
            "max_bytes": _IMPORT_MAX_BYTES,
        }, status_code=400)

    # Resolve the target channel now so the hidden field on the
    # preview form carries it through to /parent/add/confirm exactly
    # like the URL-based add flow does.
    resolved_channel_name = (new_channel_name or payload.channel_name or "").strip()
    resolved_channel_id_str = (target_channel_id or "").strip()

    urls = [entry.url for entry in payload.entries]
    videos, failed = content.fetch_previews_for_urls(urls)

    if not videos:
        err = (
            "None of the imported URLs could be fetched. Check your network "
            "and yt-dlp install. "
            f"({len(failed)} URL(s) failed.)"
        )
        return templates.TemplateResponse(request, "parent/sharing_import.html", {
            "request": request,
            "channels": channels_svc.list(),
            "error": err,
            "max_bytes": _IMPORT_MAX_BYTES,
        }, status_code=400)

    # The preview page expects a single "source" label at the top; for
    # imports we synthesise one from the decoded channel name.
    source_title = payload.channel_name or "Imported list"
    # source_url is used for channel/playlist confirms to pass through to
    # SourceRepository. For imports there's no single URL, so use a
    # tagged sentinel that the confirm handler accepts as "video-type" add.
    source_url = f"curatables://import/{payload.source_format}"

    # Render the same preview template /parent/add uses.
    return templates.TemplateResponse(request, "parent/content_preview.html", {
        "request": request,
        "url_type": "video",         # treat each entry independently
        "videos": videos,
        "source_url": source_url,
        "source_title": source_title,
        "channels": channels_svc.list(),
        "default_resolution": content.config.storage.default_resolution,
        "import_context": {
            "format": payload.source_format,
            "failed_count": len(failed),
            "target_channel_id": resolved_channel_id_str,
            "new_channel_name": resolved_channel_name,
        },
    })
