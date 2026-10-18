"""Parent inbox for the videos kids asked for (the wishlist)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse

from starlette.concurrency import run_in_threadpool

from app.dependencies import (
    get_content_service, get_wishlist_repo, get_wishlist_service, require_parent,
)
from app.models import ViewerContext
from app.repositories.wishlist_repo import WishlistRepository
from app.services.content import ContentService
from app.services.wishlist import WishlistService

router = APIRouter(prefix="/parent/requests", tags=["parent-requests"])

# Messages the page shows after returning with ?notice=<key>.
NOTICES = {
    "added": "Added. It is downloading now; the Content tab shows its progress.",
    "rejected": "Done. That video won't be offered to that kid again.",
    "removed": "Removed. The kid can ask for that video again.",
    "undone": "Removed from the library and turned down for that kid.",
    "unavailable": "YouTube won't serve that video (private, removed or restricted), so it can't be added.",
}


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def requests_page(request: Request,
                  notice: str = "",
                  viewer: ViewerContext = Depends(require_parent),
                  repo: WishlistRepository = Depends(get_wishlist_repo),
                  content: ContentService = Depends(get_content_service)):
    return request.app.state.templates.TemplateResponse(request, "parent/requests.html", {
        "request": request,
        "channel_names": {c.id: c.name for c in content.list_channels()},
        "notice": NOTICES.get(notice),
        "pending": repo.list_by_status("pending"),
        "pending_total": repo.count_pending(),
        "decided": repo.list_decided(),
    })


@router.post("/{request_id}/reject")
def reject_request(request_id: int,
                   viewer: ViewerContext = Depends(require_parent),
                   repo: WishlistRepository = Depends(get_wishlist_repo)):
    item = repo.get(request_id)
    if item is not None and item.status == "pending":
        repo.decide(request_id, "rejected")
        return RedirectResponse(url="/parent/requests?notice=rejected", status_code=303)
    return RedirectResponse(url="/parent/requests", status_code=303)


@router.post("/{request_id}/remove")
def remove_request(request_id: int,
                   viewer: ViewerContext = Depends(require_parent),
                   repo: WishlistRepository = Depends(get_wishlist_repo)):
    """Drop a request without turning the video down: a waiting one
    asked by mistake, or a "Not this one" the parent takes back."""
    item = repo.get(request_id)
    if item is not None and item.status in ("pending", "rejected"):
        repo.remove(request_id)
        return RedirectResponse(url="/parent/requests?notice=removed", status_code=303)
    return RedirectResponse(url="/parent/requests", status_code=303)


@router.post("/{request_id}/undo-auto")
def undo_auto_added(request_id: int,
                    viewer: ViewerContext = Depends(require_parent),
                    repo: WishlistRepository = Depends(get_wishlist_repo),
                    content: ContentService = Depends(get_content_service)):
    """Take back a video added without review: it leaves the library and
    this request is turned down, so it can't come back the same way.
    Other kids' requests it answered wait for the parent again."""
    item = repo.get(request_id)
    if item is None or item.status != "approved" or not item.auto_added:
        return RedirectResponse(url="/parent/requests", status_code=303)
    content.delete_video(item.video_id)
    repo.decide(request_id, "rejected")
    repo.reopen_approved(item.video_id)
    return RedirectResponse(url="/parent/requests?notice=undone", status_code=303)


@router.get("/{request_id}/review", response_class=HTMLResponse)
async def review_request(request: Request, request_id: int,
                         viewer: ViewerContext = Depends(require_parent),
                         repo: WishlistRepository = Depends(get_wishlist_repo),
                         content: ContentService = Depends(get_content_service),
                         wishlist: WishlistService = Depends(get_wishlist_service)):
    """The usual Review & add page, filled from what was stored when the
    kid asked. Only if the lookup after Ask failed is YouTube asked now."""
    item = repo.get(request_id)
    if item is not None and item.status == "pending" and not item.has_details:
        await run_in_threadpool(wishlist.fill_details, request_id)
        item = repo.get(request_id)
    if item is not None and item.status == "unavailable":
        return RedirectResponse(url="/parent/requests?notice=unavailable", status_code=303)
    if item is None or item.status != "pending":
        return RedirectResponse(url="/parent/requests", status_code=303)
    meta = item.metadata()
    return request.app.state.templates.TemplateResponse(request, "parent/content_preview.html", {
        "request": request,
        "url_type": "video",
        "videos": [meta],
        "source_url": item.url,
        "source_title": meta.title,
        "channels": content.list_channels(),
        "default_resolution": content.config.storage.default_resolution,
        "wish": item,
    })
