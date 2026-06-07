"""Kid profile selection: picker, PIN entry, profile switching."""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_profile_service
from app.services.profiles import ProfileService
from app.models import ViewerContext
from app.template_utils import render_kid_template
from app.constants import AVATAR_EMOJIS

router = APIRouter(tags=["kid-profiles"])


def _viewer_for_profile(profile):
    """Build a temporary ViewerContext for rendering themed PIN page."""
    return ViewerContext(
        viewer_type="anonymous",
        profile_name=profile.name,
        display_name=profile.display_name,
        theme=profile.theme,
    )


@router.get("/profiles", response_class=HTMLResponse)
def profile_picker(request: Request,
                   profiles_svc: ProfileService = Depends(get_profile_service)):
    profiles = profiles_svc.list()
    if len(profiles) == 1 and not profiles[0].pin:
        # Single-kid household with no PIN: zero-click auto-select.
        request.session["profile_id"] = profiles[0].id
        return RedirectResponse(url="/", status_code=302)
    # Picker has no viewer context (pre-login), use base templates.
    # When profiles is empty the template renders an empty-state
    # message with a link to /parent/profiles/ so the parent can
    # create one. Don't redirect to /, that would loop the
    # anonymous-kid middleware.
    return request.app.state.templates.TemplateResponse(request, "kid/profiles.html", {
        "request": request,
        "profiles": profiles,
        "avatars": AVATAR_EMOJIS,
    })


@router.post("/profiles/select")
def profile_select(request: Request,
                   profile_id: int = Form(...),
                   profiles_svc: ProfileService = Depends(get_profile_service)):
    profile = profiles_svc.get(profile_id)
    if not profile:
        return RedirectResponse(url="/profiles", status_code=302)
    if profile.pin:
        viewer = _viewer_for_profile(profile)
        return render_kid_template(request, viewer, "kid/pin.html", {
            "profile": profile,
            "avatar_emoji": AVATAR_EMOJIS.get(profile.avatar, "\U0001f464"),
            "error": None,
        })
    request.session["profile_id"] = profile.id
    return RedirectResponse(url="/", status_code=302)


@router.post("/profiles/pin")
def profile_pin(request: Request,
                profile_id: int = Form(...),
                pin: str = Form(...),
                profiles_svc: ProfileService = Depends(get_profile_service)):
    profile = profiles_svc.get(profile_id)
    if not profile:
        return RedirectResponse(url="/profiles", status_code=302)
    if pin.strip() == profile.pin:
        request.session["profile_id"] = profile.id
        return RedirectResponse(url="/", status_code=302)
    viewer = _viewer_for_profile(profile)
    return render_kid_template(request, viewer, "kid/pin.html", {
        "profile": profile,
        "avatar_emoji": AVATAR_EMOJIS.get(profile.avatar, "\U0001f464"),
        "error": "Wrong PIN. Try again.",
    })


@router.get("/profiles/switch")
def profile_switch(request: Request):
    request.session.pop("profile_id", None)
    return RedirectResponse(url="/profiles", status_code=302)
