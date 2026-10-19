"""Parent profile management: create, edit, delete child profiles."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import (
    require_parent, get_profile_service, get_content_service, get_kid_library_service,
    get_trusted_channel_service,
)
from app.services.profiles import ProfileService
from app.services.content import ContentService
from app.services.kid_library import KidLibraryService
from app.services.trusted_channels import TrustedChannelError, TrustedChannelService
from app.models import ViewerContext
from app.constants import (
    AVATAR_CHOICES, AVATAR_EMOJIS, PICTURE_PIN_LENGTH, PIN_PICTURES, PIN_STYLE_CHOICES,
    SEARCH_CHOICES, THEME_CHOICES,
)
from app.i18n import DEFAULT_LANGUAGE, LANGUAGES
from app.models.profile import FEATURE_SWITCHES
from app.web import form_str

router = APIRouter(prefix="/parent/profiles", tags=["parent-profiles"])


def _choice(value, choices, default: str) -> str:
    allowed = {key for key, _label in choices}
    return value if value in allowed else default


def _profile_fields(form, content: ContentService) -> dict:
    """channel_access: "all" channels, "some" (the ticked channel_ids) or
    "none" (only the kid's own channels). Ticking a channel means "some".
    auto_add: "family", "off", or one of the parent's channels."""
    channel_ids = [int(x) for x in form.getlist("channel_ids") if x.isdigit()]
    access = form.get("channel_access")
    if access == "none":
        channel_ids = []
    elif channel_ids:
        access = "some"
    elif access != "some":
        access = "all"
    return {
        "avatar": _choice(form.get("avatar"), AVATAR_CHOICES, "default"),
        "theme": _choice(form.get("theme"), THEME_CHOICES, "base"),
        "language": _choice(form.get("language"), LANGUAGES, DEFAULT_LANGUAGE),
        "search_mode": _choice(form.get("search_mode"), SEARCH_CHOICES, "disabled"),
        "channels_restricted": access != "all",
        "allowed_channel_ids": channel_ids,
        "trusted_only": _choice(form.get("trusted_only"), TRUSTED_ONLY_CHOICES, "family"),
        **_auto_add(form_str(form, "auto_add"), content),
        **{field: form.get(field) == "1" for field, _l, _d in FEATURE_SWITCHES},
    }


TRUSTED_ONLY_CHOICES = (("family", ""), ("on", ""), ("off", ""))


def _auto_add(value: str, content: ContentService) -> dict:
    """auto_add_mode and auto_add_channel_id from the form's choice; a
    channel that isn't the parent's counts as "off"."""
    if value == "off" or value.isdigit():
        ours = {c.id for c in content.list_channels() if not c.owner_profile_id}
        if value.isdigit() and int(value) in ours:
            return {"auto_add_mode": "on", "auto_add_channel_id": int(value)}
        return {"auto_add_mode": "off", "auto_add_channel_id": None}
    return {"auto_add_mode": "family", "auto_add_channel_id": None}


async def _save_trusted(form, profile_id: int, trusted: TrustedChannelService) -> str | None:
    """The form's YouTube channels part: family channels left unticked are
    switched off for this kid, unticked ones of their own are dropped, and
    a pasted channel is added for them. Returns an error, if any."""
    if form_str(form, "youtube_section") != "1":
        return None
    # Only the family channels the form showed: one added since then
    # stays as it was.
    shown = set(form.getlist("trusted_family_shown"))
    off = (trusted.switched_off_for(profile_id) - shown) | (shown - set(form.getlist("trusted_family")))
    trusted.set_switched_off(profile_id, off)
    keep = set(form.getlist("trusted_extra"))
    for ch in trusted.extra_for(profile_id):
        if ch.channel_id not in keep:
            trusted.remove(ch.channel_id, profile_id)
    text = form_str(form, "add_channel").strip()
    if not text:
        return None
    try:
        found = await run_in_threadpool(trusted.lookup, text)
        trusted.add(found.channel_id, found.name, profile_id)
    except TrustedChannelError as e:
        return f"Everything else was saved, but the channel wasn't added: {e}"
    return None


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


def _render_form(request: Request, content: ContentService, trusted: TrustedChannelService,
                 profile=None, error: str | None = None, status_code: int = 200,
                 add_channel: str = ""):
    kid_id = profile.id if profile else None
    return request.app.state.templates.TemplateResponse(request, "parent/profile_form.html", {
        "request": request,
        "profile": profile,
        "channels": content.list_channels(),
        "avatar_choices": AVATAR_CHOICES,
        "theme_choices": THEME_CHOICES,
        "search_choices": SEARCH_CHOICES,
        "pin_style_choices": PIN_STYLE_CHOICES,
        "language_choices": LANGUAGES,
        "pin_pictures": PIN_PICTURES,
        "picture_pin_length": PICTURE_PIN_LENGTH,
        "selected_channel_ids": (profile.allowed_channel_ids or []) if profile else [],
        "feature_switches": FEATURE_SWITCHES,
        "trusted_family": trusted.family(),
        "trusted_off": trusted.switched_off_for(kid_id) if kid_id else set(),
        "trusted_extra": trusted.extra_for(kid_id) if kid_id else [],
        "add_channel": add_channel,
        "wishlist_rules": request.app.state.config.wishlist,
        "error": error,
    }, status_code=status_code)


# Messages the Kids page shows after returning with ?notice=<key>.
NOTICES = {
    "added": "Kid added. Open the kid address on their tablet to start.",
    "saved": "Changes saved. They apply the next time the kid opens a page.",
    "deleted": "Kid deleted, with their comments, reactions, tags and video requests. Channels they made are yours now.",
}


@router.get("/", response_class=HTMLResponse)
def list_profiles(request: Request,
                  notice: str = "",
                  viewer: ViewerContext = Depends(require_parent),
                  profiles_svc: ProfileService = Depends(get_profile_service),
                  content: ContentService = Depends(get_content_service)):
    return request.app.state.templates.TemplateResponse(request, "parent/profiles.html", {
        "request": request,
        "profiles": profiles_svc.list(),
        "avatars": AVATAR_EMOJIS,
        "languages": dict(LANGUAGES),
        "themes": {key: label.split(" (")[0] for key, label in THEME_CHOICES},
        "channel_names": {c.id: c.name for c in content.list_channels()},
        "notice": NOTICES.get(notice),
    })


@router.get("/create", response_class=HTMLResponse)
def create_form(request: Request,
                viewer: ViewerContext = Depends(require_parent),
                content: ContentService = Depends(get_content_service),
                trusted: TrustedChannelService = Depends(get_trusted_channel_service)):
    return _render_form(request, content, trusted)


@router.post("/create")
async def create_submit(request: Request,
                        viewer: ViewerContext = Depends(require_parent),
                        profiles_svc: ProfileService = Depends(get_profile_service),
                        content: ContentService = Depends(get_content_service),
                        trusted: TrustedChannelService = Depends(get_trusted_channel_service)):
    form = await request.form()
    display_name = form_str(form, "display_name").strip()
    pin_style, pin, pin_error = _read_pin(form)
    error = "Name is required." if not display_name else pin_error
    if error:
        return _render_form(request, content, trusted, error=error, status_code=400)

    profile_id = profiles_svc.create(
        name=profiles_svc.unique_slug(display_name),
        display_name=display_name,
        pin=pin,
        pin_style=pin_style,
        **_profile_fields(form, content),
    )
    error = await _save_trusted(form, profile_id, trusted)
    if error:
        return _render_form(request, content, trusted, profiles_svc.get(profile_id),
                            error=error, status_code=400,
                            add_channel=form_str(form, "add_channel"))
    return RedirectResponse(url="/parent/profiles?notice=added", status_code=302)


@router.get("/{profile_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, profile_id: int,
              viewer: ViewerContext = Depends(require_parent),
              profiles_svc: ProfileService = Depends(get_profile_service),
              content: ContentService = Depends(get_content_service),
              trusted: TrustedChannelService = Depends(get_trusted_channel_service)):
    profile = profiles_svc.get(profile_id)
    if not profile:
        return RedirectResponse(url="/parent/profiles", status_code=302)
    return _render_form(request, content, trusted, profile)


@router.post("/{profile_id}/edit")
async def edit_submit(request: Request, profile_id: int,
                      viewer: ViewerContext = Depends(require_parent),
                      profiles_svc: ProfileService = Depends(get_profile_service),
                      content: ContentService = Depends(get_content_service),
                      trusted: TrustedChannelService = Depends(get_trusted_channel_service)):
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
        return _render_form(request, content, trusted, profile, error=error, status_code=400)

    # The slug (profiles.name) stays stable across edits; only the
    # friendly display_name is editable from the form.
    fields = {
        "display_name": form_str(form, "display_name").strip() or profile.display_name,
        **_profile_fields(form, content),
    }
    if removing:
        fields["pin"] = ""
    elif pin:
        fields["pin"] = pin
        fields["pin_style"] = pin_style
    profiles_svc.update(profile_id, **fields)
    error = await _save_trusted(form, profile_id, trusted)
    if error:
        return _render_form(request, content, trusted, profiles_svc.get(profile_id),
                            error=error, status_code=400,
                            add_channel=form_str(form, "add_channel"))
    return RedirectResponse(url="/parent/profiles?notice=saved", status_code=302)


@router.post("/{profile_id}/delete")
def delete_profile(request: Request, profile_id: int,
                   viewer: ViewerContext = Depends(require_parent),
                   profiles_svc: ProfileService = Depends(get_profile_service),
                   kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    profiles_svc.delete(profile_id)
    kid_lib.delete_profile_files(profile_id)
    return RedirectResponse(url="/parent/profiles?notice=deleted", status_code=302)
