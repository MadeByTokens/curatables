"""Parent inbox for the videos kids asked for (the wishlist)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_wishlist_repo, require_parent
from app.models import ViewerContext
from app.repositories.wishlist_repo import WishlistRepository

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
