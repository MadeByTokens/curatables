"""Parent home dashboard — the /parent/ landing view.

Extracted from parent_content/router.py where it grew alongside the
CRUD routes and was making that file unwieldy. This is read-only view
logic only; every action (add video, edit, moderate) still lives in
its own feature.
"""

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse

from app.dependencies import (
    require_parent, get_channel_service, get_content_service, get_stats_service,
)
from app.services.channels import ChannelService
from app.services.content import ContentService
from app.services.stats import StatsService
from app.models import ViewerContext

router = APIRouter(prefix="/parent", tags=["parent-dashboard"])


@router.get("/", response_class=HTMLResponse)
def dashboard(request: Request,
              viewer: ViewerContext = Depends(require_parent),
              content: ContentService = Depends(get_content_service),
              stats: StatsService = Depends(get_stats_service),
              channels_svc: ChannelService = Depends(get_channel_service)):
    _, total = content.list_all(page=1, per_page=1)
    overview = stats.dashboard_overview()
    setup = {
        "has_channel": bool(channels_svc.list()),
        "has_profile": bool(overview["per_kid"]),
        "has_video": total > 0,
    }
    return request.app.state.templates.TemplateResponse(request,
        "parent/dashboard.html", {
            "request": request,
            "video_count": total,
            "setup": setup,
            "kid_url": f"{request.url.scheme}://{request.url.netloc}/",
            **overview,
        })
