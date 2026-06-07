from __future__ import annotations
"""Parent profile management: create, edit, delete child profiles."""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_parent, get_profile_service, get_content_service
from app.services.profiles import ProfileService
from app.services.content import ContentService
from app.models import ViewerContext
from app.constants import AVATAR_CHOICES, THEME_CHOICES, SEARCH_CHOICES

router = APIRouter(prefix="/parent/profiles", tags=["parent-profiles"])


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
    channels = content.list_channels()
    return request.app.state.templates.TemplateResponse(request, "parent/profile_form.html", {
        "request": request,
        "profile": None,
        "channels": channels,
        "avatar_choices": AVATAR_CHOICES,
        "theme_choices": THEME_CHOICES,
        "search_choices": SEARCH_CHOICES,
        "selected_channel_ids": [],
        "error": None,
    })


@router.post("/create")
async def create_submit(request: Request,
                        viewer: ViewerContext = Depends(require_parent),
                        profiles_svc: ProfileService = Depends(get_profile_service),
                        content: ContentService = Depends(get_content_service)):
    form = await request.form()
    display_name = form.get("display_name", "").strip()
    if not display_name:
        channels = content.list_channels()
        return request.app.state.templates.TemplateResponse(request, "parent/profile_form.html", {
            "request": request,
            "profile": None,
            "channels": channels,
            "avatar_choices": AVATAR_CHOICES,
            "theme_choices": THEME_CHOICES,
            "search_choices": SEARCH_CHOICES,
            "selected_channel_ids": [],
            "error": "Name is required.",
        })

    channel_ids = [int(x) for x in form.getlist("channel_ids") if x.isdigit()]
    profiles_svc.create(
        name=profiles_svc.unique_slug(display_name),
        display_name=display_name,
        pin=form.get("pin", "").strip()[:10],
        avatar=form.get("avatar", "default"),
        theme=form.get("theme", "base"),
        search_mode=form.get("search_mode", "disabled"),
        allowed_channel_ids=channel_ids or None,
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
    channels = content.list_channels()
    return request.app.state.templates.TemplateResponse(request, "parent/profile_form.html", {
        "request": request,
        "profile": profile,
        "channels": channels,
        "avatar_choices": AVATAR_CHOICES,
        "theme_choices": THEME_CHOICES,
        "search_choices": SEARCH_CHOICES,
        "selected_channel_ids": profile.allowed_channel_ids or [],
        "error": None,
    })


@router.post("/{profile_id}/edit")
async def edit_submit(request: Request, profile_id: int,
                      viewer: ViewerContext = Depends(require_parent),
                      profiles_svc: ProfileService = Depends(get_profile_service)):
    form = await request.form()
    channel_ids = [int(x) for x in form.getlist("channel_ids") if x.isdigit()]
    # The slug (profiles.name) stays stable across edits; only the
    # friendly display_name is editable from the form.
    profiles_svc.update(
        profile_id,
        display_name=form.get("display_name", "").strip(),
        pin=form.get("pin", "").strip()[:10],
        avatar=form.get("avatar", "default"),
        theme=form.get("theme", "base"),
        search_mode=form.get("search_mode", "disabled"),
        allowed_channel_ids=channel_ids or None,
    )
    return RedirectResponse(url="/parent/profiles", status_code=302)


@router.post("/{profile_id}/delete")
def delete_profile(request: Request, profile_id: int,
                   viewer: ViewerContext = Depends(require_parent),
                   profiles_svc: ProfileService = Depends(get_profile_service)):
    profiles_svc.delete(profile_id)
    return RedirectResponse(url="/parent/profiles", status_code=302)
