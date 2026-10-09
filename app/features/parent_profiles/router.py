from __future__ import annotations
"""Parent profile management: create, edit, delete child profiles."""

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_parent, get_profile_service, get_content_service
from app.services.profiles import ProfileService
from app.services.content import ContentService
from app.models import ViewerContext
from app.constants import AVATAR_CHOICES, THEME_CHOICES, SEARCH_CHOICES
from app.models.profile import FEATURE_SWITCHES

router = APIRouter(prefix="/parent/profiles", tags=["parent-profiles"])


def _choice(value, choices, default: str) -> str:
    allowed = {key for key, _label in choices}
    return value if value in allowed else default


def _profile_fields(form) -> dict:
    channel_ids = [int(x) for x in form.getlist("channel_ids") if x.isdigit()]
    return {
        "avatar": _choice(form.get("avatar"), AVATAR_CHOICES, "default"),
        "theme": _choice(form.get("theme"), THEME_CHOICES, "base"),
        "search_mode": _choice(form.get("search_mode"), SEARCH_CHOICES, "disabled"),
        "channels_restricted": (form.get("channels_restricted") == "1"
                                or bool(channel_ids)),
        "allowed_channel_ids": channel_ids,
        **{field: form.get(field) == "1" for field, _l, _d in FEATURE_SWITCHES},
    }


def _pin_error(pin: str) -> str | None:
    if pin and not (pin.isdigit() and 4 <= len(pin) <= 6):
        return "PIN must be 4 to 6 digits."
    return None


def _render_form(request: Request, content: ContentService, profile=None,
                 error: str | None = None, status_code: int = 200):
    return request.app.state.templates.TemplateResponse(request, "parent/profile_form.html", {
        "request": request,
        "profile": profile,
        "channels": content.list_channels(),
        "avatar_choices": AVATAR_CHOICES,
        "theme_choices": THEME_CHOICES,
        "search_choices": SEARCH_CHOICES,
        "selected_channel_ids": (profile.allowed_channel_ids or []) if profile else [],
        "feature_switches": FEATURE_SWITCHES,
        "error": error,
    }, status_code=status_code)


@router.get("/", response_class=HTMLResponse)
def list_profiles(request: Request,
                  viewer: ViewerContext = Depends(require_parent),
                  profiles_svc: ProfileService = Depends(get_profile_service)):
    profiles = profiles_svc.list()
    avatars = dict(AVATAR_CHOICES)
    return request.app.state.templates.TemplateResponse(request, "parent/profiles.html", {
        "request": request,
        "profiles": profiles,
        "avatars": avatars,
    })


@router.get("/create", response_class=HTMLResponse)
def create_form(request: Request,
                viewer: ViewerContext = Depends(require_parent),
                content: ContentService = Depends(get_content_service)):
    return _render_form(request, content)


@router.post("/create")
async def create_submit(request: Request,
                        viewer: ViewerContext = Depends(require_parent),
                        profiles_svc: ProfileService = Depends(get_profile_service),
                        content: ContentService = Depends(get_content_service)):
    form = await request.form()
    display_name = form.get("display_name", "").strip()
    pin = form.get("pin", "").strip()
    error = "Name is required." if not display_name else _pin_error(pin)
    if error:
        return _render_form(request, content, error=error, status_code=400)

    profiles_svc.create(
        name=profiles_svc.unique_slug(display_name),
        display_name=display_name,
        pin=pin,
        **_profile_fields(form),
    )
    return RedirectResponse(url="/parent/profiles", status_code=302)


@router.get("/{profile_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, profile_id: int,
              viewer: ViewerContext = Depends(require_parent),
              profiles_svc: ProfileService = Depends(get_profile_service),
              content: ContentService = Depends(get_content_service)):
    profile = profiles_svc.get(profile_id)
    if not profile:
        return RedirectResponse(url="/parent/profiles", status_code=302)
    return _render_form(request, content, profile)


@router.post("/{profile_id}/edit")
async def edit_submit(request: Request, profile_id: int,
                      viewer: ViewerContext = Depends(require_parent),
                      profiles_svc: ProfileService = Depends(get_profile_service),
                      content: ContentService = Depends(get_content_service)):
    profile = profiles_svc.get(profile_id)
    if not profile:
        return RedirectResponse(url="/parent/profiles", status_code=302)
    form = await request.form()
    pin = form.get("pin", "").strip()
    error = _pin_error(pin)
    if error:
        return _render_form(request, content, profile, error=error, status_code=400)

    # The slug (profiles.name) stays stable across edits; only the
    # friendly display_name is editable from the form.
    fields = {
        "display_name": form.get("display_name", "").strip() or profile.display_name,
        **_profile_fields(form),
    }
    if form.get("remove_pin") == "1":
        fields["pin"] = ""
    elif pin:
        fields["pin"] = pin
    profiles_svc.update(profile_id, **fields)
    return RedirectResponse(url="/parent/profiles", status_code=302)


@router.post("/{profile_id}/delete")
def delete_profile(request: Request, profile_id: int,
                   viewer: ViewerContext = Depends(require_parent),
                   profiles_svc: ProfileService = Depends(get_profile_service)):
    profiles_svc.delete(profile_id)
    return RedirectResponse(url="/parent/profiles", status_code=302)
