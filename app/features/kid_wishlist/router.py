"""Kid wishlist — find new videos on YouTube and ask a grown-up."""
from __future__ import annotations

from urllib.parse import quote, quote_plus

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_channel_service, get_content_service, get_event_service, get_kid_library_service,
    get_profile_service, get_thumbnail_service, get_wishlist_repo, get_wishlist_service,
    require_kid_feature,
)
from app.i18n import gettext
from app.models import ViewerContext
from app.repositories.wishlist_repo import WishlistRepository
from app.services.channels import ChannelService
from app.services.content import ContentService
from app.services.events import EventService
from app.services.ids import make_video_id
from app.services.images import image_extension
from app.services.kid_library import KidLibraryService
from app.services.profiles import ProfileService
from app.services.thumbnails import ThumbnailService
from app.services.wishlist import (
    SORTS, WishlistError, WishlistService, blocked_word_in, fill_details_later,
)
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-wishlist"])

_wishlist = require_kid_feature("allow_wishlist")
_SEEN_KEY = "wishlist_seen"
_SEEN_MAX = 96
TITLE_MAX = 200
DESCRIPTION_MAX = 1000
CHANNEL_NAME_MAX = 64
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


def _with_asked(url: str, how: str = "1") -> str:
    return url + ("&" if "?" in url else "?") + f"asked={how}"


def _render(request: Request, viewer: ViewerContext, repo: WishlistRepository,
            content: ContentService, **ctx):
    # A video that was added and then deleted or hidden (by a grown-up or
    # by the kid) leaves the list: its Watch button would lead nowhere.
    mine = [w for w in repo.list_for_profile(viewer.kid_id, limit=24)
            if w.status != "approved" or not content.gone_for_viewer(w.video_id, viewer)]
    # A turned-down request whose title now has one of the parent's
    # blocked words is listed without its title and picture.
    words = request.app.state.config.wishlist.blocked_words
    hidden = {w.id for w in mine if w.status != "approved"
              and blocked_word_in(words, f"{w.title}\n{w.original_title}", near=True)}
    context = {
        "here": _here(request),
        "query": "", "mode": "videos", "sort": "", "sorts": (), "results": [],
        "channels": [], "page": 1, "has_more": False, "base": "",
        "channel_id": "", "channel_title": "",
        "error": None, "error_kind": "", "asked": False, "auto_added": False,
        "my_requests": mine,
        "hidden_requests": hidden,
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
                        content: ContentService = Depends(get_content_service),
                        events: EventService = Depends(get_event_service)):
    query = " ".join(q.split())[:100]
    mode = mode if mode in ("videos", "channels") else "videos"
    sort = sort if sort in SORTS[mode] else SORTS[mode][0]
    ctx: dict = {"query": query, "mode": mode, "sort": sort, "sorts": SORTS[mode],
                 "asked": asked == "1", "auto_added": asked == "auto",
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
    return _render(request, viewer, repo, content, **ctx)


@router.get("/wishlist/channel/{channel_id}", response_class=HTMLResponse)
async def wishlist_channel(request: Request, channel_id: str,
                           q: str = Query(""),
                           sort: str = Query("newest"),
                           page: int = Query(1, ge=1, le=99),
                           viewer: ViewerContext = Depends(_wishlist),
                           service: WishlistService = Depends(get_wishlist_service),
                           repo: WishlistRepository = Depends(get_wishlist_repo),
                           content: ContentService = Depends(get_content_service),
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
    return _render(request, viewer, repo, content, **ctx)


@router.post("/wishlist/ask")
async def wishlist_ask(request: Request,
                       background: BackgroundTasks,
                       raw_id: str = Form(...),
                       back: str = Form("/wishlist"),
                       viewer: ViewerContext = Depends(_wishlist),
                       service: WishlistService = Depends(get_wishlist_service),
                       repo: WishlistRepository = Depends(get_wishlist_repo),
                       content: ContentService = Depends(get_content_service),
                       events: EventService = Depends(get_event_service)):
    try:
        request_id, partial = await run_in_threadpool(service.ask, viewer.kid_id, raw_id)
    except WishlistError as e:
        return _render(request, viewer, repo, content, **_refusal(viewer, e))
    if partial:
        background.add_task(fill_details_later, request.app.state.db_connect, service.backend,
                            service.thumbnails, request.app.state.config, request_id)
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


def _ask_page(request: Request, viewer: ViewerContext, item, back: str,
              content: ContentService, error: str = "", new_channel: str = "",
              status_code: int = 200):
    response = render_kid_template(request, viewer, "kid/wishlist_ask.html", {
        "item": item, "back": _back(back), "nav_active": "wishlist",
        "my_channels": content.channels_for_viewer(viewer),
        "description": item.description[:DESCRIPTION_MAX],
        "title_max": TITLE_MAX, "description_max": DESCRIPTION_MAX,
        "channel_name_max": CHANNEL_NAME_MAX,
        "error": error, "new_channel": new_channel or item.suggested_channel,
    })
    response.status_code = status_code
    return response


@router.get("/wishlist/asked/{request_id}", response_class=HTMLResponse)
def wishlist_asked(request: Request, request_id: int,
                   back: str = Query("/wishlist"),
                   viewer: ViewerContext = Depends(_wishlist),
                   repo: WishlistRepository = Depends(get_wishlist_repo),
                   content: ContentService = Depends(get_content_service)):
    item = _own_pending(repo, viewer, request_id)
    if item is None:
        return RedirectResponse(url="/wishlist", status_code=303)
    return _ask_page(request, viewer, item, back, content)


@router.post("/wishlist/asked/{request_id}")
def wishlist_choose(request: Request, request_id: int,
                    title: str = Form(""),
                    description: str = Form(""),
                    channel_id: str = Form(""),
                    new_channel: str = Form(""),
                    back: str = Form("/wishlist"),
                    viewer: ViewerContext = Depends(_wishlist),
                    repo: WishlistRepository = Depends(get_wishlist_repo),
                    content: ContentService = Depends(get_content_service),
                    channels_svc: ChannelService = Depends(get_channel_service),
                    profiles: ProfileService = Depends(get_profile_service),
                    service: WishlistService = Depends(get_wishlist_service),
                    kid_lib: KidLibraryService = Depends(get_kid_library_service),
                    events: EventService = Depends(get_event_service)):
    item = _own_pending(repo, viewer, request_id)
    how = "1"
    if item is not None:
        mine = content.channels_for_viewer(viewer)
        visible = {c.id for c in mine}
        target = int(channel_id) if channel_id.isdigit() and int(channel_id) in visible else None
        title = " ".join(title.split())[:TITLE_MAX]
        if not title or title == " ".join(item.title.split())[:TITLE_MAX]:
            title = item.title
        description = description.strip()[:DESCRIPTION_MAX]
        # A new channel's name wins over the picked one. A kid who may
        # make channels gets their own now; any other kid suggests it.
        name = " ".join(new_channel.split())[:CHANNEL_NAME_MAX]
        suggested = ""
        error = ""
        if name:
            same = next((c for c in mine if c.name.casefold() == name.casefold()), None)
            if same is not None:
                target = same.id
            elif viewer.allow_customize:
                try:
                    created = channels_svc.create_for_kid(name, owner_profile_id=viewer.kid_id)
                    target = created.id
                    events.log("channel_created", profile_id=viewer.profile_id,
                               data={"channel_id": created.id, "name": created.name})
                except ValueError as e:
                    error = str(e)
            else:
                target, suggested = None, name
        repo.choose(request_id, title, description, target, suggested)
        if error:
            return _ask_page(request, viewer, repo.get(request_id), back, content,
                             error=error, new_channel=name, status_code=400)
        into = service.auto_add_into(profiles.get(viewer.kid_id))
        if into is not None and _auto_add(request_id, into, viewer, repo, content,
                                          channels_svc, service, kid_lib):
            how = "auto"
            events.log("wishlist_auto_added", video_id=item.video_id,
                       profile_id=viewer.profile_id, data={"channel_id": into})
    spot = f"#ask-{quote(item.raw_id, safe='')}" if item is not None else ""
    return RedirectResponse(url=_with_asked(_back(back), how) + spot, status_code=303)


def _auto_add(request_id: int, into: int, viewer: ViewerContext, repo: WishlistRepository,
              content: ContentService, channels_svc: ChannelService,
              service: WishlistService, kid_lib: KidLibraryService) -> bool:
    """Add a sent request to the library straight away, into the channel
    the parent set for this kid, if its YouTube channel is one of the
    kid's trusted channels. YouTube's full details are fetched first when only the search
    result is stored, so the rules see the description too. A kid's own
    channel they picked gets the video as well."""
    channel = channels_svc.get(into)
    if channel is None or channel.owner_profile_id is not None:
        return False
    item = repo.get(request_id)
    if item is not None and item.status == "pending" and not item.has_details:
        service.fill_details(request_id)
        item = repo.get(request_id)
    if item is None or item.status != "pending" or not item.has_details:
        return False
    meta = item.metadata()
    if not service.may_skip_review(meta, service.kid_rules(item.profile_id)):
        return False
    if service.auto_adds_left(item.profile_id) <= 0:
        # Today's allowance is used up: it waits like any request (the kid
        # sees nothing different) and the parent sees why.
        repo.mark_held_by_limit(request_id)
        return False
    source_id = content.create_source("video", meta.extractor, meta.video_id,
                                      meta.title, item.url)
    content.add_video(meta, source_id=source_id, channel_id=into,
                      title_override=item.title, description_override=item.description)
    repo.approve_video(make_video_id(meta.extractor, meta.video_id))
    repo.mark_auto_added(request_id)
    picked = channels_svc.get(item.target_channel_id) if item.target_channel_id else None
    if picked is not None and picked.id is not None and picked.owner_profile_id == viewer.profile_id:
        kid_lib.bookmark_video(viewer.kid_id, picked.id, item.video_id)
    return True


@router.post("/wishlist/asked/{request_id}/cancel")
def wishlist_cancel(request: Request, request_id: int,
                    back: str = Form("/wishlist"),
                    viewer: ViewerContext = Depends(_wishlist),
                    repo: WishlistRepository = Depends(get_wishlist_repo),
                    events: EventService = Depends(get_event_service)):
    """The kid changed their mind (or tapped Ask by mistake): the request
    goes, and the video can be asked for again."""
    item = _own_pending(repo, viewer, request_id)
    if item is not None:
        repo.remove(request_id)
        events.log("wishlist_cancel", video_id=item.video_id, profile_id=viewer.profile_id)
    spot = f"#ask-{quote(item.raw_id, safe='')}" if item is not None else ""
    return RedirectResponse(url=_back(back) + spot, status_code=303)


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
