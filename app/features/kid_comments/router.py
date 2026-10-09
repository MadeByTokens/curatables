"""Kid comments: post and reply to comments on videos.

Plain <form> POST with a PRG redirect by default; when the watch page
posts via XHR (X-Requested-With) the endpoint returns the rendered
comment node instead, so the new comment can be inserted in place
without a full reload — a reload would re-create the <video> element and
restart playback.
"""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import RedirectResponse, PlainTextResponse

from app.dependencies import (
    get_comment_service, get_content_service, require_kid_feature,
)
from app.services.content import ContentService, VideoNotFound
from app.services.comments import CommentService
from app.services.rate_limit import RateLimitExceeded
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-comments"])


def _is_xhr(request: Request) -> bool:
    return request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"


@router.post("/watch/{video_id}/comment")
def post_comment(request: Request, video_id: str,
                 body: str = Form(...),
                 parent_comment_id: int = Form(None),
                 viewer: ViewerContext = Depends(require_kid_feature("allow_comments", allow_parent=True)),
                 content: ContentService = Depends(get_content_service),
                 comments: CommentService = Depends(get_comment_service)):
    xhr = _is_xhr(request)
    try:
        content.get_video_for_viewer(video_id, viewer)
    except VideoNotFound:
        return PlainTextResponse("", status_code=404)
    if viewer.profile_id or viewer.is_parent:
        try:
            comment_id = comments.post(video_id, body, viewer, parent_comment_id)
        except RateLimitExceeded:
            if xhr:
                # 429 lets the watch page show an inline "slow down" hint
                # without losing the playing video to a reload.
                return PlainTextResponse("rate_limited", status_code=429)
            return RedirectResponse(
                url=f"/watch/{video_id}?rl=1#comments", status_code=303)
        if xhr:
            comment = comments.get(comment_id) if comment_id else None
            if comment is None:
                return PlainTextResponse("", status_code=204)  # empty/invalid body
            return render_kid_template(
                request, viewer, "kid/_comment_response.html",
                {"comment": comment, "video_id": video_id})
    if xhr:
        return PlainTextResponse("", status_code=204)
    return RedirectResponse(url=f"/watch/{video_id}#comments", status_code=302)
