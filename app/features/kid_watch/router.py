"""Kid video player page."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_viewer, get_content_service, get_reaction_service, get_comment_service, get_kid_library_service
from app.services.content import ContentService, VideoNotFound
from app.services.reactions import ReactionService
from app.services.comments import CommentService
from app.services.kid_library import KidLibraryService
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-watch"])


COMMENTS_PER_PAGE = 20


@router.get("/watch/{video_id}", response_class=HTMLResponse)
def watch(request: Request, video_id: str,
          comments_page: int = Query(1, ge=1, alias="cp"),
          viewer: ViewerContext = Depends(get_viewer),
          content: ContentService = Depends(get_content_service),
          reactions: ReactionService = Depends(get_reaction_service),
          comments_svc: CommentService = Depends(get_comment_service),
          kid_lib: KidLibraryService = Depends(get_kid_library_service)):
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
        patched = kid_lib.apply_overrides(viewer.profile_id, [video])
        video = patched[0]
        tags = kid_lib.tags_for_video(viewer.profile_id, video_id)

    my_reaction = None
    reaction_counts = {}
    if viewer.profile_id:
        my_reaction = reactions.get_for_video(viewer.profile_id, video_id)
    reaction_counts = reactions.get_counts(video_id)

    next_episode = None
    if video.episode_key is not None and video.channel_id is not None:
        candidate = content.video_repo.next_episode(video.channel_id, video.episode_key)
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
        "comments_total_pages": comments_total_pages,
        "tags": tags,
        "next_episode": next_episode,
    })
