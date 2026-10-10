"""Kid wishlist — find new videos on YouTube and ask a grown-up."""
from __future__ import annotations

from urllib.parse import quote, quote_plus

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_content_service, get_event_service, get_thumbnail_service, get_wishlist_repo,
    get_wishlist_service, require_kid_feature,
)
from app.i18n import gettext
from app.models import ViewerContext
from app.repositories.wishlist_repo import WishlistRepository
from app.services.content import ContentService
from app.services.events import EventService
from app.services.images import image_extension
from app.services.thumbnails import ThumbnailService
from app.services.wishlist import SORTS, WishlistError, WishlistService
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-wishlist"])

_wishlist = require_kid_feature("allow_wishlist")
_SEEN_KEY = "wishlist_seen"
_SEEN_MAX = 96
TITLE_MAX = 200
DESCRIPTION_MAX = 1000
_IMAGE_TYPES = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp",
                ".gif": "image/gif"}


def _seen(request: Request, profile_id: int) -> list[str]:
    seen = request.session.get(_SEEN_KEY)
    if not isinstance(seen, dict) or seen.get("profile") != profile_id:
        return []
    return list(seen.get("ids", []))


def _remember_seen(request: Request, profile_id: int, video_ids: list[str]) -> None:
    older = [v for v in _seen(request, profile_id) if v not in video_ids]
    request.session[_SEEN_KEY] = {"profile": profile_id,
                                  "ids": (video_ids + older)[:_SEEN_MAX]}


def _here(request: Request) -> str:
    url = request.url.remove_query_params("asked")
    return url.path + (f"?{url.query}" if url.query else "")


def _back(value: str) -> str:
    """Only ever back to a Find new page."""
    if value.startswith("/wishlist") and not value.startswith("/wishlist/asked"):
        return value
    return "/wishlist"


def _with_asked(url: str) -> str:
    return url + ("&" if "?" in url else "?") + "asked=1"


def _render(request: Request, viewer: ViewerContext, repo: WishlistRepository, **ctx):
    context = {
        "here": _here(request),
        "query": "", "mode": "videos", "sort": "", "sorts": (), "results": [],
        "channels": [], "page": 1, "has_more": False, "base": "",
        "channel_id": "", "channel_title": "",
        "error": None, "error_kind": "", "asked": False,
        "my_requests": repo.list_for_profile(viewer.kid_id, limit=24),
        "nav_active": "wishlist",
    }
    context.update(ctx)
    return render_kid_template(request, viewer, "kid/wishlist.html", context)


def _refusal(viewer: ViewerContext, e: WishlistError) -> dict:
    return {"error": gettext(viewer.language, str(e)), "error_kind": e.kind}


@router.get("/wishlist", response_class=HTMLResponse)
async def wishlist_page(request: Request,
                        q: str = Query(""),
                        mode: str = Query("videos"),
                        sort: str = Query("best"),
                        page: int = Query(1, ge=1, le=99),
                        asked: str = Query(""),
                        viewer: ViewerContext = Depends(_wishlist),
                        service: WishlistService = Depends(get_wishlist_service),
                        repo: WishlistRepository = Depends(get_wishlist_repo),
                        events: EventService = Depends(get_event_service)):
    query = " ".join(q.split())[:100]
    mode = mode if mode in ("videos", "channels") else "videos"
    sort = sort if sort in SORTS[mode] else SORTS[mode][0]
    ctx: dict = {"query": query, "mode": mode, "sort": sort, "sorts": SORTS[mode],
                 "asked": asked == "1",
                 "base": f"/wishlist?q={quote_plus(query)}&mode={mode}"}
    if query:
        try:
            found = await run_in_threadpool(service.search, viewer.kid_id, query, page, mode, sort)
            ctx.update(results=found.results, channels=found.channels, page=found.page,
                       has_more=found.has_more)
            _remember_seen(request, viewer.kid_id, [r.video_id for r in found.results]
                           + [c.thumb_id for c in found.channels])
        except WishlistError as e:
            ctx.update(_refusal(viewer, e))
        events.log("wishlist_search", profile_id=viewer.profile_id,
                   data={"query": query, "mode": mode, "sort": sort, "page": page,
                         "result_count": len(ctx.get("results", [])) + len(ctx.get("channels", [])),
                         "refused": ctx.get("error_kind") or None})
    return _render(request, viewer, repo, **ctx)


@router.get("/wishlist/channel/{channel_id}", response_class=HTMLResponse)
async def wishlist_channel(request: Request, channel_id: str,
                           q: str = Query(""),
                           sort: str = Query("newest"),
                           page: int = Query(1, ge=1, le=99),
                           viewer: ViewerContext = Depends(_wishlist),
                           service: WishlistService = Depends(get_wishlist_service),
                           repo: WishlistRepository = Depends(get_wishlist_repo),
                           events: EventService = Depends(get_event_service)):
    query = " ".join(q.split())[:100]
    sort = sort if sort in SORTS["channel"] else "newest"
    ctx: dict = {"query": query, "mode": "channels", "sort": sort, "sorts": SORTS["channel"],
                 "channel_id": channel_id,
                 "base": f"/wishlist/channel/{quote(channel_id, safe='')}?q={quote_plus(query)}"}
    try:
        found = await run_in_threadpool(service.channel, viewer.kid_id, channel_id, page, sort)
        ctx.update(results=found.results, page=found.page, has_more=found.has_more,
                   channel_title=found.title)
        _remember_seen(request, viewer.kid_id, [r.video_id for r in found.results])
    except WishlistError as e:
        ctx.update(_refusal(viewer, e))
    events.log("wishlist_channel", profile_id=viewer.profile_id,
               data={"channel": ctx.get("channel_title", ""), "page": page,
                     "result_count": len(ctx.get("results", [])),
                     "refused": ctx.get("error_kind") or None})
    return _render(request, viewer, repo, **ctx)


@router.post("/wishlist/ask")
async def wishlist_ask(request: Request,
                       raw_id: str = Form(...),
                       back: str = Form("/wishlist"),
                       viewer: ViewerContext = Depends(_wishlist),
                       service: WishlistService = Depends(get_wishlist_service),
                       repo: WishlistRepository = Depends(get_wishlist_repo),
                       events: EventService = Depends(get_event_service)):
    try:
        request_id = await run_in_threadpool(service.ask, viewer.kid_id, raw_id)
    except WishlistError as e:
        return _render(request, viewer, repo, **_refusal(viewer, e))
    item = repo.get(request_id)
    events.log("wishlist_request", video_id=item.video_id if item else None,
               profile_id=viewer.profile_id)
    if item is not None:
        _remember_seen(request, viewer.kid_id, [item.video_id])
    return RedirectResponse(url=f"/wishlist/asked/{request_id}?back={quote_plus(_back(back))}",
                            status_code=303)


def _own_pending(repo: WishlistRepository, viewer: ViewerContext, request_id: int):
    item = repo.get(request_id)
    if item is None or item.profile_id != viewer.kid_id or item.status != "pending":
        return None
    return item


@router.get("/wishlist/asked/{request_id}", response_class=HTMLResponse)
def wishlist_asked(request: Request, request_id: int,
                   back: str = Query("/wishlist"),
                   viewer: ViewerContext = Depends(_wishlist),
                   repo: WishlistRepository = Depends(get_wishlist_repo),
                   content: ContentService = Depends(get_content_service)):
    item = _own_pending(repo, viewer, request_id)
    if item is None:
        return RedirectResponse(url="/wishlist", status_code=303)
    return render_kid_template(request, viewer, "kid/wishlist_ask.html", {
        "item": item, "back": _back(back), "nav_active": "wishlist",
        "my_channels": content.channels_for_viewer(viewer),
        "description": item.description[:DESCRIPTION_MAX],
        "title_max": TITLE_MAX, "description_max": DESCRIPTION_MAX,
    })


@router.post("/wishlist/asked/{request_id}")
def wishlist_choose(request: Request, request_id: int,
                    title: str = Form(""),
                    description: str = Form(""),
                    channel_id: str = Form(""),
                    back: str = Form("/wishlist"),
                    viewer: ViewerContext = Depends(_wishlist),
                    repo: WishlistRepository = Depends(get_wishlist_repo),
                    content: ContentService = Depends(get_content_service)):
    item = _own_pending(repo, viewer, request_id)
    if item is not None:
        visible = {c.id for c in content.channels_for_viewer(viewer)}
        target = int(channel_id) if channel_id.isdigit() and int(channel_id) in visible else None
        title = " ".join(title.split())[:TITLE_MAX]
        if not title or title == " ".join(item.title.split())[:TITLE_MAX]:
            title = item.title
        description = description.strip()[:DESCRIPTION_MAX]
        if description == item.description[:DESCRIPTION_MAX].strip():
            description = item.description
        repo.choose(request_id, title, description, target)
    return RedirectResponse(url=_with_asked(_back(back)), status_code=303)


@router.get("/wishlist/thumb/{video_id}")
def wishlist_thumb(request: Request, video_id: str,
                   viewer: ViewerContext = Depends(_wishlist),
                   repo: WishlistRepository = Depends(get_wishlist_repo),
                   thumbnails: ThumbnailService = Depends(get_thumbnail_service)):
    allowed = (video_id in _seen(request, viewer.kid_id)
               or video_id in repo.statuses_for(viewer.kid_id))
    path = thumbnails.get_path(video_id) if allowed else None
    if path is None or not path.is_file():
        return Response(status_code=404)
    with open(path, "rb") as f:
        ext = image_extension(f.read(12))
    if ext is None:
        return Response(status_code=404)
    return FileResponse(path, media_type=_IMAGE_TYPES[ext],
                        headers={"Cache-Control": "private, max-age=86400"})
