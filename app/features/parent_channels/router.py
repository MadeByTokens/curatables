from __future__ import annotations
"""Parent channel management: create, edit, delete internal channels."""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import require_parent, get_channel_service, get_profile_service
from app.services.channels import ChannelService
from app.services.profiles import ProfileService
from app.models import ViewerContext

router = APIRouter(prefix="/parent/channels", tags=["parent-channels"])


@router.get("/", response_class=HTMLResponse)
def list_channels(request: Request,
                  viewer: ViewerContext = Depends(require_parent),
                  channels_svc: ChannelService = Depends(get_channel_service),
                  profiles_svc: ProfileService = Depends(get_profile_service)):
    channels_with_counts = channels_svc.list_with_counts()
    # Map owner_profile_id -> display_name for the "(by X)" badge.
    owner_names: dict[int, str] = {}
    for ch, _ in channels_with_counts:
        owner_id = getattr(ch, "owner_profile_id", None)
        if owner_id and owner_id not in owner_names:
            profile = profiles_svc.get(owner_id)
            if profile is not None:
                owner_names[owner_id] = profile.display_name or profile.name
    # Plain list of channels for the delete-reassign dropdown.
    all_channels = [ch for ch, _ in channels_with_counts]
    return request.app.state.templates.TemplateResponse(request, "parent/channels.html", {
        "request": request,
        "channels_with_counts": channels_with_counts,
        "owner_names": owner_names,
        "all_channels": all_channels,
    })


@router.post("/create")
def create_channel(request: Request,
                   name: str = Form(...),
                   description: str = Form(""),
                   viewer: ViewerContext = Depends(require_parent),
                   channels_svc: ChannelService = Depends(get_channel_service)):
    name = name.strip()
    if name:
        channels_svc.create(name, description.strip())
    return RedirectResponse(url="/parent/channels/", status_code=302)


@router.get("/{channel_id}/edit", response_class=HTMLResponse)
def edit_form(request: Request, channel_id: int,
              viewer: ViewerContext = Depends(require_parent),
              channels_svc: ChannelService = Depends(get_channel_service),
              profiles_svc: ProfileService = Depends(get_profile_service)):
    channel = channels_svc.get(channel_id)
    if not channel:
        return RedirectResponse(url="/parent/channels/", status_code=302)
    profiles = profiles_svc.list()
    return request.app.state.templates.TemplateResponse(request, "parent/channel_edit.html", {
        "request": request,
        "channel": channel,
        "profiles": profiles,
    })


@router.post("/{channel_id}/edit")
def edit_submit(request: Request, channel_id: int,
                name: str = Form(...),
                description: str = Form(""),
                position: int = Form(0),
                owner_profile_id: str = Form(""),
                viewer: ViewerContext = Depends(require_parent),
                channels_svc: ChannelService = Depends(get_channel_service)):
    name = name.strip()
    if not name:
        return RedirectResponse(url=f"/parent/channels/{channel_id}/edit", status_code=302)
    owner_id: int | None = None
    owner_raw = (owner_profile_id or "").strip()
    if owner_raw and owner_raw.isdigit():
        owner_id = int(owner_raw)
    channels_svc.update(channel_id, name=name,
                        description=description.strip(),
                        position=max(0, position),
                        owner_profile_id=owner_id)
    return RedirectResponse(url="/parent/channels/", status_code=302)


@router.post("/{channel_id}/delete")
def delete_channel(request: Request, channel_id: int,
                   reassign_to: str = Form(""),
                   viewer: ViewerContext = Depends(require_parent),
                   channels_svc: ChannelService = Depends(get_channel_service)):
    reassign_id: int | None = None
    raw = (reassign_to or "").strip()
    if raw and raw.isdigit():
        candidate = int(raw)
        if candidate == channel_id:
            # Refuse self-reassignment; fall through to orphan behavior.
            reassign_id = None
        elif channels_svc.get(candidate) is not None:
            reassign_id = candidate
    channels_svc.delete(channel_id, reassign_to=reassign_id)
    return RedirectResponse(url="/parent/channels/", status_code=302)
