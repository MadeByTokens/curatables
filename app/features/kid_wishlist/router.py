"""Kid wishlist — find new videos on YouTube and ask a grown-up."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import FileResponse, HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_event_service, get_thumbnail_service, get_wishlist_repo, get_wishlist_service,
    require_kid_feature,
)
from app.i18n import gettext
from app.models import ViewerContext
from app.repositories.wishlist_repo import WishlistRepository
from app.services.events import EventService
from app.services.images import image_extension
from app.services.thumbnails import ThumbnailService
from app.services.wishlist import WishlistError, WishlistService
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-wishlist"])

_wishlist = require_kid_feature("allow_wishlist")
_SEEN_KEY = "wishlist_seen"
_SEEN_MAX = 48
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


@router.get("/wishlist", response_class=HTMLResponse)
async def wishlist_page(request: Request,
                        q: str = Query(""),
                        page: int = Query(1, ge=1, le=99),
                        asked: str = Query(""),
                        viewer: ViewerContext = Depends(_wishlist),
                        service: WishlistService = Depends(get_wishlist_service),
                        repo: WishlistRepository = Depends(get_wishlist_repo),
                        events: EventService = Depends(get_event_service)):
    query = " ".join(q.split())[:100]
    results = []
    error = None
    found = None
    if query:
        try:
            found = await run_in_threadpool(service.search, viewer.kid_id, query, page)
            results = found.results
        except WishlistError as e:
            error = gettext(viewer.language, str(e))
        events.log("wishlist_search", profile_id=viewer.profile_id,
                   data={"query": query, "page": page, "result_count": len(results),
                         "refused": error is not None})
        _remember_seen(request, viewer.kid_id, [r.video_id for r in results])
    return render_kid_template(request, viewer, "kid/wishlist.html", {
        "query": query,
        "results": results,
        "page": found.page if found else 1,
        "has_more": bool(found and found.has_more),
        "error": error,
        "asked": asked == "1",
        "my_requests": repo.list_for_profile(viewer.kid_id, limit=24),
        "nav_active": "wishlist",
    })


@router.post("/wishlist/ask")
async def wishlist_ask(request: Request,
                       raw_id: str = Form(...),
                       q: str = Form(""),
                       viewer: ViewerContext = Depends(_wishlist),
                       service: WishlistService = Depends(get_wishlist_service),
                       repo: WishlistRepository = Depends(get_wishlist_repo),
                       events: EventService = Depends(get_event_service)):
    try:
        video_id = await run_in_threadpool(service.ask, viewer.kid_id, raw_id)
    except WishlistError as e:
        return render_kid_template(request, viewer, "kid/wishlist.html", {
            "query": "", "results": [], "asked": False,
            "error": gettext(viewer.language, str(e)),
            "my_requests": repo.list_for_profile(viewer.kid_id, limit=24),
            "nav_active": "wishlist",
        })
    events.log("wishlist_request", video_id=video_id, profile_id=viewer.profile_id)
    _remember_seen(request, viewer.kid_id, [video_id])
    return RedirectResponse(url="/wishlist?asked=1", status_code=303)


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
