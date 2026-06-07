"""Kid search — search within approved content."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_viewer, get_content_service, get_event_service
from app.services.content import ContentService
from app.services.events import EventService
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-search"])


@router.get("/search", response_class=HTMLResponse)
def search(request: Request,
           q: str = Query(""),
           page: int = Query(1, ge=1),
           viewer: ViewerContext = Depends(get_viewer),
           content: ContentService = Depends(get_content_service),
           events: EventService = Depends(get_event_service)):
    if viewer.search_mode == "disabled":
        return RedirectResponse(url="/", status_code=302)

    videos = []
    query = q.strip()
    if query:
        videos = content.search_for_viewer(query, viewer, page=page)
        events.log("search", profile_id=viewer.profile_id,
                   data={"query": query, "result_count": len(videos)})

    return render_kid_template(request, viewer, "kid/search.html", {
        "videos": videos,
        "query": query,
        "page": page,
    })
