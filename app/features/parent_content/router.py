"""Parent content management: add, edit, list, hide, delete, moderate."""

import logging

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse

logger = logging.getLogger(__name__)

from app.dependencies import (
    require_parent, get_content_service, get_channel_service,
    get_stats_service, get_comment_service, get_thumbnail_service,
)
from app.services.content import ContentService, ContentError
from app.services.channels import ChannelService
from app.services.thumbnails import ThumbnailService
from app.services.reactions import EMOJI_MAP
from app.services.stats import StatsService
from app.services.comments import CommentService
from app.models import ViewerContext
from urllib.parse import quote

router = APIRouter(prefix="/parent", tags=["parent-content"])

ITEMS_PER_PAGE = 30


@router.get("/content", response_class=HTMLResponse)
def content_list(request: Request,
                 page: int = Query(1, ge=1),
                 flash: str = Query(""),
                 flash_type: str = Query(""),
                 viewer: ViewerContext = Depends(require_parent),
                 content: ContentService = Depends(get_content_service),
                 channels_svc: ChannelService = Depends(get_channel_service)):
    videos, total = content.list_all(page=page, per_page=ITEMS_PER_PAGE)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    channels = channels_svc.list()
    return request.app.state.templates.TemplateResponse(request, "parent/content.html", {
        "request": request,
        "videos": videos,
        "page": page,
        "total_pages": total_pages,
        "total": total,
        "channels": channels,
        "flash": flash or None,
        "flash_type": flash_type or "success",
    })


@router.get("/add", response_class=HTMLResponse)
def add_page(request: Request,
             viewer: ViewerContext = Depends(require_parent)):
    return request.app.state.templates.TemplateResponse(request, "parent/add.html", {
        "request": request, "error": None, "success": None,
    })


@router.post("/add")
def add_fetch(request: Request,
              url: str = Form(...),
              viewer: ViewerContext = Depends(require_parent),
              content: ContentService = Depends(get_content_service)):
    templates = request.app.state.templates

    parsed = content.parse_url(url)
    if not parsed:
        return templates.TemplateResponse(request, "parent/add.html", {
            "request": request,
            "error": "Could not recognize this URL.",
            "success": None,
        })

    try:
        source_title, videos = content.fetch_preview(parsed)
    except ContentError as e:
        return templates.TemplateResponse(request, "parent/add.html", {
            "request": request, "error": str(e), "success": None,
        })

    channels = content.list_channels()
    return templates.TemplateResponse(request, "parent/content_preview.html", {
        "request": request,
        "url_type": parsed.url_type,
        "videos": videos,
        "source_url": parsed.clean_url,
        "source_title": source_title,
        "channels": channels,
        "default_resolution": content.config.storage.default_resolution,
    })


@router.post("/add/confirm")
async def add_confirm(request: Request,
                      viewer: ViewerContext = Depends(require_parent),
                      content: ContentService = Depends(get_content_service)):
    templates = request.app.state.templates
    form = await request.form()

    url_type = form.get("url_type", "video")
    source_url = form.get("source_url", "")
    source_title = form.get("source_title", "Unknown")

    channel_id = content.resolve_channel(
        form.get("channel_id", ""),
        form.get("new_channel_name", ""),
    )
    resolution = form.get("resolution", content.config.storage.default_resolution)

    video_ids = form.getlist("video_ids")
    import re
    from app.backends.base import VideoMetadata

    # Relaxed ID validation: any safe slug-shaped string up to 128
    # chars. The narrow 11-char YouTube-ID regex would drop Vimeo's
    # numeric IDs, Dailymotion alphanumerics, PeerTube UUIDs, and
    # anything else yt-dlp knows about. The character class is
    # still tight enough that we never build a filesystem path with
    # `..` / `/` / `\x00` in it.
    _SAFE_VIDEO_ID = re.compile(r"^[a-zA-Z0-9._-]{1,128}$")

    added = 0
    skipped = 0
    unreviewed = 0
    for vid in video_ids:
        if not _SAFE_VIDEO_ID.match(vid):
            continue
        # Gate: parent must have ticked "I have already fully watched this video" on the
        # preview page. Client-side JS already disables the Add button
        # until every box is checked, but enforce it here too so bypassing
        # the script can't queue a download of something the parent never
        # watched.
        if not form.get(f"watched_{vid}"):
            unreviewed += 1
            continue
        extractor = (form.get(f"extractor_{vid}", "") or "").lower()
        original_url = form.get(f"original_url_{vid}", "")
        info = VideoMetadata(
            video_id=vid,
            title=form.get(f"original_title_{vid}", "Unknown"),
            channel=form.get(f"channel_name_{vid}", ""),
            duration=int(form.get(f"duration_{vid}", "0") or "0"),
            view_count=int(form.get(f"view_count_{vid}", "0") or "0"),
            upload_date=form.get(f"upload_date_{vid}", ""),
            description=form.get(f"original_desc_{vid}", ""),
            thumbnail_url=form.get(f"thumb_{vid}", ""),
            extractor=extractor,
            original_url=original_url,
        )
        # Source row: for a single-video add, the source's external_id
        # is the raw video ID; for channel/playlist adds, it's the
        # source URL (yt-dlp's canonical handle for the container).
        source_external_id = vid if url_type == "video" else source_url
        source_id = content.create_source(
            url_type, extractor, source_external_id,
            source_title, source_url,
        )
        result = content.add_video(
            info, source_id=source_id, channel_id=channel_id,
            resolution=resolution,
            title_override=form.get(f"title_{vid}"),
            description_override=form.get(f"desc_{vid}"),
        )
        if result:
            added += 1
        else:
            skipped += 1

    msg = f"Added {added} video(s). Downloading in the background — check the Content tab for progress."
    if skipped:
        msg += f" {skipped} already existed (skipped)."
    if unreviewed:
        msg += (f" {unreviewed} skipped because the \"I have already fully watched this video\" "
                f"box was not ticked.")
    return templates.TemplateResponse(request, "parent/add.html", {
        "request": request, "error": None, "success": msg,
    })


@router.get("/content/{video_id}/edit", response_class=HTMLResponse)
def edit_page(request: Request, video_id: str,
              viewer: ViewerContext = Depends(require_parent),
              content: ContentService = Depends(get_content_service)):
    try:
        video = content.get_video(video_id)
    except Exception as e:
        logger.warning("Failed to load video %s for editing: %s", video_id, e)
        return RedirectResponse(url="/parent/content", status_code=302)
    channels = content.list_channels()
    return request.app.state.templates.TemplateResponse(request, "parent/content_edit.html", {
        "request": request, "video": video, "channels": channels, "success": None,
    })


@router.post("/content/{video_id}/edit")
async def edit_save(request: Request, video_id: str,
                    viewer: ViewerContext = Depends(require_parent),
                    content: ContentService = Depends(get_content_service),
                    thumbnails: ThumbnailService = Depends(get_thumbnail_service)):
    form = await request.form()
    title = (form.get("title") or "").strip()
    description = (form.get("description") or "").strip()
    channel_id = (form.get("channel_id") or "").strip()
    new_channel_name = (form.get("new_channel_name") or "").strip()
    resolution = (form.get("resolution") or "").strip()

    ch_id = content.resolve_channel(channel_id, new_channel_name)
    fields = {"title": title, "description": description}
    if ch_id is not None:
        fields["channel_id"] = ch_id
    if resolution:
        fields["resolution"] = resolution
    # Unchecked checkboxes don't appear in the form body at all —
    # we treat "present and truthy" as True, everything else as False.
    fields["keep_forever"] = 1 if form.get("keep_forever") else 0
    video = content.update_video(video_id, **fields)

    # Optional thumbnail replacement. Overwrites the canonical
    # videos/{id}/thumb.jpg so every viewer (including kids without
    # their own per-profile override) picks it up on the next load.
    thumbnail = form.get("thumbnail")
    success_msg = "Saved."
    if thumbnail and hasattr(thumbnail, "read"):
        data = await thumbnail.read()
        if data:
            thumbnails.save_uploaded(video_id, data)
            success_msg = "Saved. New thumbnail will appear on next refresh."

    channels = content.list_channels()
    return request.app.state.templates.TemplateResponse(request,
        "parent/content_edit.html", {
            "request": request, "video": video, "channels": channels,
            "success": success_msg,
        })


@router.post("/content/{video_id}/hide")
def hide(request: Request, video_id: str,
         viewer: ViewerContext = Depends(require_parent),
         content: ContentService = Depends(get_content_service)):
    content.hide_video(video_id)
    return RedirectResponse(url="/parent/content", status_code=302)


@router.post("/content/{video_id}/activate")
def activate(request: Request, video_id: str,
             viewer: ViewerContext = Depends(require_parent),
             content: ContentService = Depends(get_content_service)):
    content.activate_video(video_id)
    return RedirectResponse(url="/parent/content", status_code=302)


@router.post("/content/{video_id}/delete")
def delete(request: Request, video_id: str,
           viewer: ViewerContext = Depends(require_parent),
           content: ContentService = Depends(get_content_service)):
    content.delete_video(video_id)
    return RedirectResponse(url="/parent/content", status_code=302)


def _redirect_with_flash(page: int, message: str, flash_type: str = "success"):
    url = f"/parent/content?page={page}&flash={quote(message)}&flash_type={flash_type}"
    return RedirectResponse(url=url, status_code=302)


@router.post("/content/bulk")
async def bulk_action(request: Request,
                      viewer: ViewerContext = Depends(require_parent),
                      content: ContentService = Depends(get_content_service),
                      channels_svc: ChannelService = Depends(get_channel_service)):
    form = await request.form()
    video_ids = form.getlist("video_ids")
    action = (form.get("action") or "").strip()
    page_str = (form.get("page") or "1").strip()
    try:
        page = max(1, int(page_str))
    except ValueError:
        page = 1

    if not video_ids:
        return _redirect_with_flash(page, "Pick at least one video first.", "error")

    if action == "move":
        target_raw = (form.get("target_channel_id") or "").strip()
        if not target_raw.isdigit():
            return _redirect_with_flash(
                page, "Pick a destination channel first.", "error")
        target_id = int(target_raw)
        target = channels_svc.get(target_id)
        if target is None:
            return _redirect_with_flash(
                page, "That destination channel doesn't exist.", "error")
        moved = 0
        for vid in video_ids:
            try:
                content.update_video(vid, channel_id=target_id)
                moved += 1
            except Exception as e:
                logger.warning("Bulk move failed for %s: %s", vid, e)
        return _redirect_with_flash(
            page, f"Moved {moved} video(s) to {target.name}.")

    if action == "hide":
        for vid in video_ids:
            content.hide_video(vid)
        return _redirect_with_flash(page, f"Hid {len(video_ids)} video(s).")

    if action == "unhide":
        for vid in video_ids:
            content.activate_video(vid)
        return _redirect_with_flash(
            page, f"Unhid {len(video_ids)} video(s).")

    if action == "delete":
        for vid in video_ids:
            content.delete_video(vid)
        return _redirect_with_flash(
            page, f"Deleted {len(video_ids)} video(s).")

    return _redirect_with_flash(page, f"Unknown action '{action}'.", "error")


@router.get("/content/{video_id}", response_class=HTMLResponse)
def content_detail(request: Request, video_id: str,
                   cp: int = Query(1, ge=1),
                   viewer: ViewerContext = Depends(require_parent),
                   content: ContentService = Depends(get_content_service),
                   stats: StatsService = Depends(get_stats_service)):
    try:
        video = content.get_video(video_id)
    except Exception:
        return RedirectResponse(url="/parent/content", status_code=302)
    detail = stats.video_detail(video_id, viewer=viewer, comments_page=cp)
    return request.app.state.templates.TemplateResponse(request,
        "parent/content_detail.html", {
            "request": request,
            "video": video,
            "emoji_lookup": dict(EMOJI_MAP),
            **detail,
        })


@router.post("/content/{video_id}/comment")
def post_comment_on_video(request: Request, video_id: str,
                          body: str = Form(""),
                          parent_comment_id: int = Form(None),
                          viewer: ViewerContext = Depends(require_parent),
                          comments: CommentService = Depends(get_comment_service)):
    from app.services.rate_limit import RateLimitExceeded
    if body.strip():
        try:
            comments.post(video_id, body, viewer, parent_comment_id)
        except RateLimitExceeded:
            return RedirectResponse(
                url=f"/parent/content/{video_id}?rl=1#comments",
                status_code=303)
    return RedirectResponse(
        url=f"/parent/content/{video_id}#comments", status_code=302)
