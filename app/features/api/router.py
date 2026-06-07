"""API endpoints for kid UI (event logging)."""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import Response

from app.dependencies import get_viewer, get_event_service, get_reaction_service
from app.services.events import EventService
from app.services.reactions import ReactionService
from app.models import ViewerContext

router = APIRouter(prefix="/api", tags=["api"])


@router.post("/log")
def log_event(request: Request,
              event: str = Form(...),
              video_id: str = Form(None),
              watch_seconds: int = Form(0),
              viewer: ViewerContext = Depends(get_viewer),
              events: EventService = Depends(get_event_service)):
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
          viewer: ViewerContext = Depends(get_viewer),
          reactions: ReactionService = Depends(get_reaction_service)):
    if not viewer.profile_id:
        return Response(status_code=403)
    reactions.react(viewer.profile_id, video_id, emoji)
    return Response(status_code=204)
