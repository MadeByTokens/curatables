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


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def requests_page(request: Request,
                  viewer: ViewerContext = Depends(require_parent),
                  repo: WishlistRepository = Depends(get_wishlist_repo)):
    return request.app.state.templates.TemplateResponse(request, "parent/requests.html", {
        "request": request,
        "pending": repo.list_by_status("pending"),
        "decided": (repo.list_by_status("approved", limit=10)
                    + repo.list_by_status("rejected", limit=10)),
    })


@router.post("/{request_id}/reject")
def reject_request(request_id: int,
                   viewer: ViewerContext = Depends(require_parent),
                   repo: WishlistRepository = Depends(get_wishlist_repo)):
    item = repo.get(request_id)
    if item is not None and item.status == "pending":
        repo.decide(request_id, "rejected")
    return RedirectResponse(url="/parent/requests", status_code=303)


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
