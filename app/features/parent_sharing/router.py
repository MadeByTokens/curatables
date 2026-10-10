"""Shared-curation routes: export a channel / import a .ytc or .txt file.

Export hangs off /parent/channels/{id}/export?format=ytc|txt|pdf;
import gets its own /parent/channels/import form. A successful import
renders the /parent/add preview page in place, so reviewing works the
same whether the videos came from a URL or a file.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request, UploadFile, File
from starlette.concurrency import run_in_threadpool
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
    videos = content.list_channel_videos(channel_id)

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


def _import_form(request: Request, channels_svc: ChannelService,
                 error: str | None = None, status_code: int = 200):
    return request.app.state.templates.TemplateResponse(request, "parent/sharing_import.html", {
        "request": request,
        "channels": channels_svc.list(),
        "error": error,
        "max_bytes": _IMPORT_MAX_BYTES,
    }, status_code=status_code)


@router.get("/import", response_class=HTMLResponse)
def import_form(request: Request,
                viewer: ViewerContext = Depends(require_parent),
                channels_svc: ChannelService = Depends(get_channel_service)):
    return _import_form(request, channels_svc)


@router.post("/import")
async def import_submit(request: Request,
                        file: UploadFile = File(None),
                        pasted: str = Form(""),
                        target_channel_id: str = Form(""),
                        new_channel_name: str = Form(""),
                        viewer: ViewerContext = Depends(require_parent),
                        channels_svc: ChannelService = Depends(get_channel_service),
                        content: ContentService = Depends(get_content_service)):
    # The uploaded file wins over the paste-in textarea.
    raw: bytes = b""
    filename = ""
    if file is not None and file.filename:
        raw = await file.read()
        filename = file.filename
    elif pasted.strip():
        raw = pasted.encode("utf-8")
        filename = "pasted.txt"

    if not raw:
        return _import_form(request, channels_svc,
                            "Pick a file or paste a URL list first.", 400)

    if len(raw) > _IMPORT_MAX_BYTES:
        return _import_form(request, channels_svc, (
            f"File is too large ({len(raw)/1024/1024:.1f} MB). "
            f"Maximum is {_IMPORT_MAX_BYTES/1024/1024:.0f} MB."), 413)

    # The file extension decides; without one, JSON content means .ytc.
    try:
        if filename.lower().endswith(".ytc") or raw.lstrip().startswith(b"{"):
            payload = decode_ytc(raw)
        else:
            payload = parse_text(raw)
    except SharingError as e:
        return _import_form(request, channels_svc, str(e), 400)

    if not payload.entries:
        return _import_form(request, channels_svc, "No URLs found in this file.", 400)

    urls = [entry.url for entry in payload.entries]
    videos, failed = await run_in_threadpool(content.fetch_previews_for_urls, urls)

    if not videos:
        return _import_form(request, channels_svc, (
            "None of the imported URLs could be fetched. Check your network "
            f"and yt-dlp install. ({len(failed)} URL(s) failed.)"), 400)

    # The preview form carries the target channel through to
    # /parent/add/confirm, like the URL-based add flow. There is no single
    # source URL, so a tagged one stands in; each entry is added as a video.
    return request.app.state.templates.TemplateResponse(request, "parent/content_preview.html", {
        "request": request,
        "url_type": "video",
        "videos": videos,
        "source_url": f"curatables://import/{payload.source_format}",
        "source_title": payload.channel_name or "Imported list",
        "channels": channels_svc.list(),
        "default_resolution": content.config.storage.default_resolution,
        "import_context": {
            "format": payload.source_format,
            "failed_count": len(failed),
            "target_channel_id": target_channel_id.strip(),
            "new_channel_name": (new_channel_name or payload.channel_name or "").strip(),
        },
    })
