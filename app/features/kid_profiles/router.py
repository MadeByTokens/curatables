"""Kid profile selection: picker, PIN entry, profile switching."""

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_pin_throttle, get_profile_service
from app.services.auth import PIN_LIMITER, PinThrottle
from app.services.profiles import ProfileService
from app.models import ViewerContext
from app.template_utils import render_kid_template
from app.constants import AVATAR_EMOJIS, PICTURE_PIN_LENGTH, PIN_PICTURES
from app.i18n import gettext

router = APIRouter(tags=["kid-profiles"])


def _viewer_for_profile(profile):
    """Build a temporary ViewerContext for rendering themed PIN page."""
    return ViewerContext(
        viewer_type="anonymous",
        profile_name=profile.name,
        display_name=profile.display_name,
        theme=profile.theme,
        language=profile.language,
    )


# Where a device goes once a kid is signed in. A browser that drops the
# session cookie (a private tab that blocks cookies) arrives without the
# kid and gets the picker with an explanation, instead of being sent
# round to the picker for ever.
SIGNED_IN_URL = "/profiles?signed_in=1"


def _enter_kid_mode(request: Request, profile_id: int | None) -> None:
    """Select a kid profile and drop any parent login on this device."""
    if profile_id is None:
        raise ValueError("profile has no id")
    request.session.pop("parent_authenticated", None)
    request.session["profile_id"] = profile_id


@router.get("/profiles", response_class=HTMLResponse)
def profile_picker(request: Request,
                   signed_in: bool = Query(False),
                   profiles_svc: ProfileService = Depends(get_profile_service)):
    if signed_in and request.session.get("profile_id"):
        return RedirectResponse(url="/", status_code=302)
    profiles = profiles_svc.list()
    if len(profiles) == 1 and not profiles[0].pin and not signed_in:
        # Single-kid household with no PIN: zero-click auto-select.
        _enter_kid_mode(request, profiles[0].id)
        return RedirectResponse(url=SIGNED_IN_URL, status_code=302)
    # With no profiles the page links to /parent/profiles/; redirecting
    # to / instead would loop through the anonymous-kid redirect.
    return request.app.state.templates.TemplateResponse(request, "kid/profiles.html", {
        "request": request,
        "profiles": profiles,
        "avatars": AVATAR_EMOJIS,
        "cookies_blocked": signed_in,
    })


def _pin_page(request: Request, profile, error: str | None = None,
              locked: bool = False):
    return render_kid_template(request, _viewer_for_profile(profile), "kid/pin.html", {
        "profile": profile,
        "avatar_emoji": AVATAR_EMOJIS.get(profile.avatar, AVATAR_EMOJIS["default"]),
        "error": gettext(profile.language, error) if error else None,
        "locked": locked,
        "pin_pictures": PIN_PICTURES,
        "picture_pin_length": PICTURE_PIN_LENGTH,
        "lang": profile.language,
    })


@router.post("/profiles/select")
def profile_select(request: Request,
                   profile_id: int = Form(...),
                   profiles_svc: ProfileService = Depends(get_profile_service)):
    profile = profiles_svc.get(profile_id)
    if not profile:
        return RedirectResponse(url="/profiles", status_code=302)
    if profile.pin:
        return _pin_page(request, profile)
    _enter_kid_mode(request, profile.id)
    return RedirectResponse(url=SIGNED_IN_URL, status_code=302)


@router.post("/profiles/pin")
def profile_pin(request: Request,
                profile_id: int = Form(...),
                pin: str = Form(...),
                profiles_svc: ProfileService = Depends(get_profile_service),
                pin_throttle: PinThrottle = Depends(get_pin_throttle)):
    profile = profiles_svc.get(profile_id)
    if not profile:
        return RedirectResponse(url="/profiles", status_code=302)
    # Keyed by device as well, so one sibling's wrong guesses don't lock
    # the profile on everyone else's tablet.
    limiter_key = (profile.id, request.client.host if request.client else "")
    if pin_throttle.blocked(profile.id) or not PIN_LIMITER.check(limiter_key):
        return _pin_page(request, profile,
                         "Too many tries. Wait a little, then try again.", locked=True)
    if profiles_svc.check_pin(profile, pin):
        PIN_LIMITER.reset(limiter_key)
        pin_throttle.succeeded(profile.id)
        _enter_kid_mode(request, profile.id)
        return RedirectResponse(url=SIGNED_IN_URL, status_code=302)
    pin_throttle.failed(profile.id)
    return _pin_page(request, profile, "Wrong PIN. Try again.")


@router.get("/profiles/switch")
def profile_switch(request: Request):
    request.session.pop("profile_id", None)
    return RedirectResponse(url="/profiles", status_code=302)
