"""Trusted YouTube channels: the family list, each kid's own additions,
and the family's defaults for "only trusted channels" and "add without
asking"."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.config import Config
from app.dependencies import (
    get_config, get_content_service, get_profile_service, get_trusted_channel_service,
    require_parent,
)
from app.models import ViewerContext
from app.services.content import ContentService
from app.services.profiles import ProfileService
from app.services.trusted_channels import TrustedChannelError, TrustedChannelService

router = APIRouter(prefix="/parent/trusted-channels", tags=["parent-trusted"])

PAGE = "/parent/trusted-channels"

# Messages the page shows after returning with ?notice=<key>.
NOTICES = {
    "trusted": "Channel added.",
    "untrusted": "Channel removed.",
    "saved": "Saved.",
}


def _back(back: str) -> str:
    """Only this site's parent pages."""
    if back.startswith("/parent/") and not back.startswith("//") and "\\" not in back:
        return back
    return PAGE


def _with_notice(url: str, notice: str) -> str:
    path, _, fragment = url.partition("#")
    path += ("&" if "?" in path else "?") + f"notice={notice}"
    return path + (f"#{fragment}" if fragment else "")


def _profile_id(value: str, profiles: ProfileService) -> int | None:
    """A kid's id, or None for the whole family (or an unknown kid)."""
    if value.isdigit() and profiles.get(int(value)) is not None:
        return int(value)
    return None


def _own_channels(content: ContentService) -> list:
    return [c for c in content.list_channels() if not c.owner_profile_id]


def _page(request: Request, config: Config, trusted: TrustedChannelService,
          profiles: ProfileService, content: ContentService, find: str = "",
          notice: str = "", error: str | None = None, status_code: int = 200):
    kids = profiles.list()
    family = trusted.family()
    names = {c.channel_id: c.name for c in family}
    per_kid = []
    for kid in kids:
        if kid.id is None:
            continue
        off = trusted.switched_off_for(kid.id)
        per_kid.append({
            "kid": kid,
            "extra": trusted.extra_for(kid.id),
            "off": [names[cid] for cid in sorted(off) if cid in names],
        })
    hits, find_error = [], None
    find = " ".join(find.split())[:100]
    if find:
        hits = trusted.search(find)
        if not hits:
            find_error = "No channels found (or YouTube didn't answer). Try other words, or paste a link."
    return request.app.state.templates.TemplateResponse(request, "parent/trusted_channels.html", {
        "request": request,
        "config": config,
        "family": family,
        "kids": kids,
        "per_kid": per_kid,
        "own_channels": _own_channels(content),
        "find": find,
        "hits": hits,
        "find_error": find_error,
        "trusted_ids": set(names),
        "notice": NOTICES.get(notice),
        "error": error,
    }, status_code=status_code)


@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def trusted_page(request: Request, find: str = "", notice: str = "",
                 viewer: ViewerContext = Depends(require_parent),
                 config: Config = Depends(get_config),
                 trusted: TrustedChannelService = Depends(get_trusted_channel_service),
                 profiles: ProfileService = Depends(get_profile_service),
                 content: ContentService = Depends(get_content_service)):
    return _page(request, config, trusted, profiles, content, find=find, notice=notice)


@router.post("/settings")
def save_family_settings(only_trusted: str = Form(""),
                         auto_add_channel_id: str = Form(""),
                         auto_add_per_day: str = Form(""),
                         viewer: ViewerContext = Depends(require_parent),
                         config: Config = Depends(get_config),
                         content: ContentService = Depends(get_content_service)):
    ours = {c.id for c in _own_channels(content)}
    into = int(auto_add_channel_id) if auto_add_channel_id.isdigit() else None
    config.wishlist.only_trusted_channels = only_trusted == "1"
    config.wishlist.auto_add_channel_id = into if into in ours else None
    if auto_add_per_day.strip().isdigit():
        config.wishlist.auto_add_per_day = min(int(auto_add_per_day), 100)
    config.save()
    return RedirectResponse(url=f"{PAGE}?notice=saved", status_code=303)


@router.post("/add")
def add_channel(request: Request,
                channel: str = Form(""),
                channel_id: str = Form(""),
                name: str = Form(""),
                profile_id: str = Form(""),
                back: str = Form(PAGE),
                viewer: ViewerContext = Depends(require_parent),
                config: Config = Depends(get_config),
                trusted: TrustedChannelService = Depends(get_trusted_channel_service),
                profiles: ProfileService = Depends(get_profile_service),
                content: ContentService = Depends(get_content_service)):
    """From a search result or a request (channel_id and name), or from a
    pasted ID, @handle or link (channel), looked up on YouTube."""
    kid = _profile_id(profile_id, profiles)
    if profile_id and kid is None:
        return RedirectResponse(url=_back(back), status_code=303)
    try:
        if channel.strip():
            found = trusted.lookup(channel)
            channel_id, name = found.channel_id, found.name
        trusted.add(channel_id.strip(), name, kid)
    except TrustedChannelError as e:
        return _page(request, config, trusted, profiles, content,
                     error=str(e), status_code=400)
    return RedirectResponse(url=_with_notice(_back(back), "trusted"), status_code=303)


@router.post("/remove")
def remove_channel(channel_id: str = Form(""),
                   profile_id: str = Form(""),
                   back: str = Form(PAGE),
                   viewer: ViewerContext = Depends(require_parent),
                   trusted: TrustedChannelService = Depends(get_trusted_channel_service),
                   profiles: ProfileService = Depends(get_profile_service)):
    kid = _profile_id(profile_id, profiles)
    if profile_id and kid is None:
        return RedirectResponse(url=_back(back), status_code=303)
    trusted.remove(channel_id.strip(), kid)
    return RedirectResponse(url=_with_notice(_back(back), "untrusted"), status_code=303)
