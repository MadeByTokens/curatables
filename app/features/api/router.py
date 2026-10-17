"""API endpoints for kid UI (event logging), and the clock beacon."""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import JSONResponse, Response

from app.config import Config
from app.dependencies import (
    get_config, get_viewer, get_event_service, get_reaction_service, get_content_service,
    require_kid_feature,
)
from app.services.content import ContentService, VideoNotFound
from app.services.events import EventService
from app.services.reactions import ReactionService
from app.models import ViewerContext
from app.services.family_time import valid_timezone
from app.services.rate_limit import RateLimiter

router = APIRouter(prefix="/api", tags=["api"])

PLAYER_EVENTS = {"video_view", "video_play", "video_complete"}

# A player sends a handful of these per video; anything far beyond that
# is a script filling the database.
EVENT_LIMITER = RateLimiter(max_events=120, window_seconds=600)


@router.post("/clock")
def clock_beacon(request: Request,
                 now: float = Form(0),
                 tz: str = Form(""),
                 viewer: ViewerContext = Depends(get_viewer),
                 config: Config = Depends(get_config)):
    """static/js/clock.js: the device's time (ms) while the box's clock may
    be behind, and its time zone, which a parent's browser sets once. Only
    signed-in kids and parents count."""
    if now > 0 and (viewer.is_parent or viewer.profile_id):
        request.app.state.clock.offer(now / 1000, by_parent=viewer.is_parent)
    if viewer.is_parent and not config.server.timezone and valid_timezone(tz):
        config.server.timezone = tz
        config.save()
    return Response(status_code=204)


@router.post("/log")
def log_event(request: Request,
              event: str = Form(...),
              video_id: str = Form(None),
              watch_seconds: int = Form(0),
              viewer: ViewerContext = Depends(get_viewer),
              content: ContentService = Depends(get_content_service),
              events: EventService = Depends(get_event_service)):
    if not viewer.profile_id or event not in PLAYER_EVENTS or not video_id:
        return Response(status_code=204)
    if not EVENT_LIMITER.check(viewer.profile_id):
        return Response(status_code=204)
    try:
        content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return Response(status_code=204)
    if event == "video_play":
        content.mark_played(video_id)
    data = {}
    if watch_seconds:
        data["watch_seconds"] = max(0, min(watch_seconds, 86400))
    events.log(event, video_id=video_id,
               profile_id=viewer.profile_id, data=data)
    return Response(status_code=204)


@router.post("/react")
def react(request: Request,
          video_id: str = Form(...),
          emoji: str = Form(...),
          viewer: ViewerContext = Depends(require_kid_feature("allow_reactions")),
          content: ContentService = Depends(get_content_service),
          reactions: ReactionService = Depends(get_reaction_service)):
    if not viewer.profile_id:
        return Response(status_code=403)
    try:
        content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return Response(status_code=404)
    mine = reactions.toggle(viewer.profile_id, video_id, emoji)
    return JSONResponse({"mine": mine, "counts": reactions.get_counts(video_id)})
