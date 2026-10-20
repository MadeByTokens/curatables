"""Kid search — search within approved content."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import (
    get_content_service, get_event_service, get_kid_library_service, get_viewer,
)
from app.services.content import ContentService
from app.services.events import EventService
from app.services.kid_library import KidLibraryService
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-search"])


@router.get("/search", response_class=HTMLResponse)
def search(request: Request,
           q: str = Query(""),
           page: int = Query(1, ge=1),
           viewer: ViewerContext = Depends(get_viewer),
           content: ContentService = Depends(get_content_service),
           events: EventService = Depends(get_event_service),
           kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    if viewer.search_mode == "disabled":
        return RedirectResponse(url="/", status_code=302)

    videos = []
    close = False
    query = q.strip()
    if query:
        videos = content.search_for_viewer(query, viewer, page=page)
        # Nothing spelled that way: videos with words close to it.
        if not videos and (page == 1 or not content.search_for_viewer(query, viewer)):
            videos = content.search_for_viewer(query, viewer, page=page, close=True)
            close = bool(videos)
        if viewer.is_child and viewer.profile_id:
            videos = kid_lib.apply_overrides(viewer.profile_id, videos)
        events.log("search", profile_id=viewer.profile_id,
                   data={"query": query, "result_count": len(videos), "close": close})

    return render_kid_template(request, viewer, "kid/search.html", {
        "videos": videos,
        "query": query,
        "close": close,
        "page": page,
    })
