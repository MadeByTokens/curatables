from __future__ import annotations
"""Parent profile management: create, edit, delete child profiles."""

from fastapi import APIRouter, Depends, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_parent, get_profile_service, get_content_service
from app.services.profiles import ProfileService
from app.services.content import ContentService
from app.models import ViewerContext
from app.constants import (
    AVATAR_CHOICES, PICTURE_PIN_LENGTH, PIN_PICTURES, PIN_STYLE_CHOICES,
    SEARCH_CHOICES, THEME_CHOICES,
)
from app.models.profile import FEATURE_SWITCHES
from app.web import form_str

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


def _read_pin(form) -> tuple[str, str, str | None]:
    """(pin_style, new PIN or "", error) from the PIN part of the form."""
    style = _choice(form_str(form, "pin_style"), PIN_STYLE_CHOICES, "digits")
    if style == "pictures":
        allowed = {digit for digit, _emoji, _name in PIN_PICTURES}
        picks = [form_str(form, f"pin_pic_{i}") for i in range(1, PICTURE_PIN_LENGTH + 1)]
        if not any(picks):
            return style, "", None
        if not all(p in allowed for p in picks):
            return style, "", f"Pick all {PICTURE_PIN_LENGTH} pictures."
        return style, "".join(picks), None
    pin = form_str(form, "pin").strip()
    if pin and not (pin.isdigit() and 4 <= len(pin) <= 6):
        return style, "", "PIN must be 4 to 6 digits."
    return style, pin, None


def _render_form(request: Request, content: ContentService, profile=None,
                 error: str | None = None, status_code: int = 200):
    return request.app.state.templates.TemplateResponse(request, "parent/profile_form.html", {
        "request": request,
        "profile": profile,
        "channels": content.list_channels(),
        "avatar_choices": AVATAR_CHOICES,
        "theme_choices": THEME_CHOICES,
        "search_choices": SEARCH_CHOICES,
        "pin_style_choices": PIN_STYLE_CHOICES,
        "pin_pictures": PIN_PICTURES,
        "picture_pin_length": PICTURE_PIN_LENGTH,
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
    display_name = form_str(form, "display_name").strip()
    pin_style, pin, pin_error = _read_pin(form)
    error = "Name is required." if not display_name else pin_error
    if error:
        return _render_form(request, content, error=error, status_code=400)

    profiles_svc.create(
        name=profiles_svc.unique_slug(display_name),
        display_name=display_name,
        pin=pin,
        pin_style=pin_style,
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
    pin_style, pin, error = _read_pin(form)
    removing = form.get("remove_pin") == "1"
    if (not error and profile.pin and not pin and not removing
            and pin_style != profile.pin_style):
        error = "You changed the kind of PIN: choose the new PIN, or tick Remove the PIN."
    if error:
        return _render_form(request, content, profile, error=error, status_code=400)

    # The slug (profiles.name) stays stable across edits; only the
    # friendly display_name is editable from the form.
    fields = {
        "display_name": form_str(form, "display_name").strip() or profile.display_name,
        **_profile_fields(form),
    }
    if removing:
        fields["pin"] = ""
    elif pin:
        fields["pin"] = pin
        fields["pin_style"] = pin_style
    profiles_svc.update(profile_id, **fields)
    return RedirectResponse(url="/parent/profiles", status_code=302)


@router.post("/{profile_id}/delete")
def delete_profile(request: Request, profile_id: int,
                   viewer: ViewerContext = Depends(require_parent),
                   profiles_svc: ProfileService = Depends(get_profile_service)):
    profiles_svc.delete(profile_id)
    return RedirectResponse(url="/parent/profiles", status_code=302)
