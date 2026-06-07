"""Parent usage statistics and comment moderation."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_parent, get_stats_service, get_comment_service
from app.services.stats import StatsService
from app.services.comments import CommentService
from app.services.reactions import EMOJI_MAP
from app.models import ViewerContext

router = APIRouter(prefix="/parent", tags=["parent-stats"])

_VALID_WINDOWS = ("today", "7d", "all")
_EMOJI_LOOKUP = dict(EMOJI_MAP)


@router.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request,
               window: str = Query("7d"),
               viewer: ViewerContext = Depends(require_parent),
               stats: StatsService = Depends(get_stats_service)):
    if window not in _VALID_WINDOWS:
        window = "7d"
    kpis = stats.dashboard_kpis(window)
    top = stats.top_videos(window)
    profiles = stats.per_profile_summary(window)
    return request.app.state.templates.TemplateResponse(request, "parent/stats.html", {
        "request": request,
        "window": window,
        "kpis": kpis,
        "top_videos": top,
        "profiles": profiles,
        "emoji_lookup": _EMOJI_LOOKUP,
    })


@router.get("/stats/profiles/{profile_id}", response_class=HTMLResponse)
def profile_stats(request: Request, profile_id: int,
                  window: str = Query("7d"),
                  viewer: ViewerContext = Depends(require_parent),
                  stats: StatsService = Depends(get_stats_service)):
    if window not in _VALID_WINDOWS:
        window = "7d"
    detail = stats.profile_detail(profile_id, window)
    if not detail["profile"]:
        return RedirectResponse(url="/parent/stats", status_code=302)
    return request.app.state.templates.TemplateResponse(request, "parent/stats_profile.html", {
        "request": request,
        "window": window,
        "emoji_lookup": _EMOJI_LOOKUP,
        **detail,
    })


@router.post("/comments/{comment_id}/delete")
def delete_comment(request: Request, comment_id: int,
                   referer: str = Query(""),
                   viewer: ViewerContext = Depends(require_parent),
                   comments: CommentService = Depends(get_comment_service)):
    comments.delete(comment_id)
    target = referer if referer.startswith("/parent/") else "/parent/stats"
    return RedirectResponse(url=target, status_code=302)
