"""Kid video player page, and hiding a video the kid doesn't like."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import (
    get_comment_service, get_content_service, get_event_service, get_hidden_video_repo,
    get_kid_home_service, get_kid_library_service, get_reaction_service, get_viewer,
    require_kid_feature,
)
from app.repositories.hidden_video_repo import HiddenVideoRepository
from app.services.events import EventService
from app.services.kid_home import KidHomeService
from app.services.content import ContentService, VideoNotFound
from app.services.reactions import ReactionService
from app.services.comments import CommentService
from app.services.kid_library import KidLibraryService
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-watch"])


COMMENTS_PER_PAGE = 20

_hide = require_kid_feature("allow_hide")


@router.get("/watch/{video_id}", response_class=HTMLResponse)
def watch(request: Request, video_id: str,
          comments_page: int = Query(1, ge=1, alias="cp"),
          already: int = Query(0),
          home: str = Query(""),
          viewer: ViewerContext = Depends(get_viewer),
          content: ContentService = Depends(get_content_service),
          reactions: ReactionService = Depends(get_reaction_service),
          comments_svc: CommentService = Depends(get_comment_service),
          kid_lib: KidLibraryService = Depends(get_kid_library_service),
          kid_home: KidHomeService = Depends(get_kid_home_service)):
    try:
        video = content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        # Not ready yet: an evicted video is queued for re-download and
        # one already on its way keeps showing the self-refreshing
        # "preparing" page until it is ready.
        rehydrating = content.try_rehydrate_evicted(video_id, viewer)
        if rehydrating is not None:
            return render_kid_template(request, viewer, "kid/preparing.html", {
                "video": rehydrating,
            })
        return RedirectResponse(url="/", status_code=302)
    tags = []
    if viewer.is_child and viewer.profile_id:
        video = kid_lib.apply_overrides(viewer.profile_id, [video])[0]
        tags = kid_lib.tags_for_video(viewer.profile_id, video_id)

    my_reaction = None
    if viewer.profile_id:
        my_reaction = reactions.get_for_video(viewer.profile_id, video_id)
    reaction_counts = reactions.get_counts(video_id)

    next_episode = None
    if video.episode_key is not None and video.channel_id is not None:
        candidate = content.video_repo.next_episode(
            video.channel_id, video.episode_key,
            hidden_for=viewer.profile_id if viewer.is_child else None)
        if candidate is not None:
            try:
                next_episode = content.get_video_for_viewer(candidate.video_id, viewer)
            except VideoNotFound:
                next_episode = None

    video_comments, comments_total = comments_svc.list_for_video(
        video_id, viewer, page=comments_page, per_page=COMMENTS_PER_PAGE)
    comments_total_pages = max(
        1, (comments_total + COMMENTS_PER_PAGE - 1) // COMMENTS_PER_PAGE)

    return render_kid_template(request, viewer, "kid/watch.html", {
        "video": video,
        "my_reaction": my_reaction,
        "reaction_counts": reaction_counts,
        "reactions_list": reactions.get_emoji_list(),
        "comments": video_comments,
        "comments_page": comments_page,
        "comments_total": comments_total,
        "comments_count": comments_svc.count_for_video(video_id, viewer),
        "comments_total_pages": comments_total_pages,
        "tags": tags,
        "next_episode": next_episode,
        "already_here": bool(already),
        "on_home": viewer.is_child and kid_home.contains(viewer, video_id),
        "home_note": home if home in ("added", "full") else "",
    })


@router.post("/video/{video_id}/hide")
def hide_video(video_id: str,
               viewer: ViewerContext = Depends(_hide),
               content: ContentService = Depends(get_content_service),
               hidden: HiddenVideoRepository = Depends(get_hidden_video_repo),
               events: EventService = Depends(get_event_service)):
    """Gone from this kid's lists; the parent sees it under Needs your
    attention and decides whether to delete it."""
    try:
        content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return RedirectResponse(url="/", status_code=303)
    hidden.hide(viewer.kid_id, video_id)
    events.log("video_hidden", video_id=video_id, profile_id=viewer.kid_id)
    return RedirectResponse(url=f"/?hid={video_id}", status_code=303)


@router.post("/video/{video_id}/unhide")
def unhide_video(video_id: str,
                 viewer: ViewerContext = Depends(_hide),
                 hidden: HiddenVideoRepository = Depends(get_hidden_video_repo)):
    """Undo, right after hiding."""
    hidden.unhide(viewer.kid_id, video_id)
    return RedirectResponse(url=f"/watch/{video_id}", status_code=303)
