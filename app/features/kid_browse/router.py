"""Kid browsing: home grid with pagination and channel filtering."""

import re

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_viewer, get_content_service, get_profile_service,
    get_reaction_service, get_kid_library_service, get_channel_service,
    get_event_service, get_kid_home_service, require_child, require_kid_feature,
)
from app.services.content import ContentService, VideoNotFound
from app.services.events import EventService
from app.services.profiles import ProfileService
from app.services.reactions import ReactionService
from app.services.kid_home import SLOTS, KidHomeService
from app.services.kid_library import KidLibraryService
from app.services.channels import ASSET_KINDS, ChannelService, valid_color
from app.web import form_str, is_xhr
from app.features.kid_profiles.router import SIGNED_IN_URL
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-browse"])

ITEMS_PER_PAGE = 24

_customize = require_kid_feature("allow_customize")
_tags = require_kid_feature("allow_tags")


def _build_home_context(videos, content, reactions, viewer, page, total,
                        current_channel_id=None):
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    channels = content.channels_to_browse(viewer)
    video_ids = [v.video_id for v in videos]
    bulk_counts = reactions.get_bulk_counts(video_ids) if video_ids else {}
    return {
        "videos": videos,
        "channels": channels,
        "page": page,
        "total_pages": total_pages,
        "reaction_counts": bulk_counts,
        "emojis": dict(reactions.get_emoji_list()),
        "current_channel_id": current_channel_id,
    }


_VIDEO_ID = re.compile(r"[A-Za-z0-9._-]{1,200}")


@router.get("/", response_class=HTMLResponse)
def home(request: Request,
         hid: str = Query(""),
         edit: bool = Query(False),
         move: int | None = Query(None),
         viewer: ViewerContext = Depends(get_viewer),
         content: ContentService = Depends(get_content_service),
         profiles_svc: ProfileService = Depends(get_profile_service),
         reactions: ReactionService = Depends(get_reaction_service),
         kid_lib: KidLibraryService = Depends(get_kid_library_service),
         kid_home: KidHomeService = Depends(get_kid_home_service)):
    """A kid's own Home, arranged by them; anyone else sees every video."""
    if viewer.viewer_type == "anonymous":
        profiles = profiles_svc.list()
        if len(profiles) == 1 and not profiles[0].pin:
            request.session["profile_id"] = profiles[0].id
            return RedirectResponse(url=SIGNED_IN_URL, status_code=302)
        return RedirectResponse(url="/profiles", status_code=302)
    if not viewer.is_child:
        return _all_videos(request, 1, viewer, content, reactions, kid_lib)

    grid = kid_home.layout(viewer)
    on_home = kid_lib.apply_overrides(viewer.kid_id, [v for v in grid if v is not None])
    named = {v.video_id: v for v in on_home}
    slots = [named[v.video_id] if v is not None else None for v in grid]
    moving = move if move is not None and 0 <= move < len(slots) and slots[move] else None
    editing = edit or moving is not None
    if not editing:
        # Gaps the kid left stay, but nothing after the last video.
        while slots and slots[-1] is None:
            slots.pop()
    return render_kid_template(request, viewer, "kid/my_home.html", {
        "slots": slots,
        "editing": editing,
        "moving": moving,
        "customized": kid_home.customized(viewer),
        "channels": content.channels_to_browse(viewer),
        "reaction_counts": reactions.get_bulk_counts(list(named)) if named else {},
        "emojis": dict(reactions.get_emoji_list()),
        # Just hidden: say so, with an Undo for a tap by mistake.
        "just_hidden": hid if viewer.allow_hide and _VIDEO_ID.fullmatch(hid) else "",
    })


@router.get("/all", response_class=HTMLResponse)
def all_videos(request: Request,
               page: int = Query(1, ge=1),
               viewer: ViewerContext = Depends(get_viewer),
               content: ContentService = Depends(get_content_service),
               reactions: ReactionService = Depends(get_reaction_service),
               kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    return _all_videos(request, page, viewer, content, reactions, kid_lib)


def _all_videos(request, page, viewer, content, reactions, kid_lib):
    videos, total = content.list_for_viewer(viewer, page=page, per_page=ITEMS_PER_PAGE)
    if viewer.is_child and viewer.profile_id:
        videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    ctx = _build_home_context(videos, content, reactions, viewer, page, total)
    return render_kid_template(request, viewer, "kid/home.html", ctx)


# --- Arranging Home ---

@router.get("/home/pick", response_class=HTMLResponse)
def home_pick(request: Request,
              slot: int = Query(..., ge=0, lt=SLOTS),
              channel: int | None = Query(None),
              page: int = Query(1, ge=1),
              viewer: ViewerContext = Depends(require_child),
              content: ContentService = Depends(get_content_service),
              kid_lib: KidLibraryService = Depends(get_kid_library_service),
              kid_home: KidHomeService = Depends(get_kid_home_service)):
    """Choose the video for an empty spot on Home."""
    videos, total = content.list_for_viewer(viewer, page=page, per_page=ITEMS_PER_PAGE,
                                            channel_id=channel)
    videos = kid_lib.apply_overrides(viewer.kid_id, videos)
    on_home = {v.video_id for v in kid_home.layout(viewer) if v is not None}
    return render_kid_template(request, viewer, "kid/home_pick.html", {
        "slot": slot,
        "videos": videos,
        "on_home": on_home,
        "channels": content.channels_to_browse(viewer),
        "current_channel_id": channel,
        "page": page,
        "total_pages": max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE),
    })


def _slot(value: str) -> int | None:
    return int(value) if value.isdigit() else None


@router.post("/home/add")
async def home_add(request: Request,
                   viewer: ViewerContext = Depends(require_child),
                   kid_home: KidHomeService = Depends(get_kid_home_service)):
    """From the picker (a spot is given) back to arranging Home; from the
    player (first free spot) back to the video."""
    form = await request.form()
    video_id = form_str(form, "video_id")
    slot = _slot(form_str(form, "slot"))
    added = kid_home.add(viewer, video_id, slot)
    if slot is not None:
        return RedirectResponse(url="/?edit=1", status_code=303)
    if not _VIDEO_ID.fullmatch(video_id):
        return RedirectResponse(url="/", status_code=303)
    return RedirectResponse(url=f"/watch/{video_id}?home={'added' if added else 'full'}",
                            status_code=303)


@router.post("/home/remove")
async def home_remove(request: Request,
                      viewer: ViewerContext = Depends(require_child),
                      kid_home: KidHomeService = Depends(get_kid_home_service)):
    form = await request.form()
    video_id = form_str(form, "video_id")
    kid_home.remove(viewer, video_id)
    if form_str(form, "back") == "watch" and _VIDEO_ID.fullmatch(video_id):
        return RedirectResponse(url=f"/watch/{video_id}", status_code=303)
    return RedirectResponse(url="/?edit=1", status_code=303)


@router.post("/home/move")
async def home_move(request: Request,
                    viewer: ViewerContext = Depends(require_child),
                    kid_home: KidHomeService = Depends(get_kid_home_service)):
    form = await request.form()
    source, target = _slot(form_str(form, "source")), _slot(form_str(form, "target"))
    if source is not None and target is not None:
        kid_home.move(viewer, source, target)
    return RedirectResponse(url="/?edit=1", status_code=303)


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request,
              viewer: ViewerContext = Depends(get_viewer)):
    return render_kid_template(request, viewer, "kid/help.html", {})


def _with_bookmarks(videos, channel_id, viewer, content, kid_lib):
    """Videos the kid saved into their own channel (from Customize), shown
    ahead of the videos that live in it. Ones the kid can no longer see
    are skipped."""
    ids, _total = kid_lib.channel_video_ids(viewer.profile_id, channel_id,
                                            per_page=ITEMS_PER_PAGE)
    present = {v.video_id for v in videos}
    saved = []
    for vid in ids:
        if vid in present:
            continue
        try:
            saved.append(content.get_video_for_viewer(vid, viewer))
        except VideoNotFound:
            continue
    return saved + videos


@router.get("/channel/{channel_id}", response_class=HTMLResponse)
def channel_view(request: Request, channel_id: int,
                 page: int = Query(1, ge=1),
                 viewer: ViewerContext = Depends(get_viewer),
                 content: ContentService = Depends(get_content_service),
                 reactions: ReactionService = Depends(get_reaction_service),
                 kid_lib: KidLibraryService = Depends(get_kid_library_service),
                 channels_svc: ChannelService = Depends(get_channel_service)):
    channel = channels_svc.get(channel_id)
    visible = content.visible_channel_ids(viewer)
    if channel is None or (visible is not None and channel_id not in visible):
        return RedirectResponse(url="/", status_code=302)
    videos, total = content.list_for_viewer(
        viewer, page=page, per_page=ITEMS_PER_PAGE, channel_id=channel_id)
    if viewer.is_child and channel.owner_profile_id == viewer.profile_id and page == 1:
        videos = _with_bookmarks(videos, channel_id, viewer, content, kid_lib)
    if viewer.is_child and viewer.profile_id:
        videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    ctx = _build_home_context(videos, content, reactions, viewer, page, total,
                              current_channel_id=channel_id)
    ctx["channel"] = channel
    return render_kid_template(request, viewer, "kid/home.html", ctx)


# --- Video customize ---

@router.get("/video/{video_id}/edit", response_class=HTMLResponse)
def video_edit_page(request: Request, video_id: str,
                    viewer: ViewerContext = Depends(_customize),
                    content: ContentService = Depends(get_content_service),
                    kid_lib: KidLibraryService = Depends(get_kid_library_service),
                    channels_svc: ChannelService = Depends(get_channel_service)):
    video = content.get_video_for_viewer(video_id, viewer)
    overrides = kid_lib.get_overrides(viewer.kid_id, video_id)
    tags = kid_lib.tags_for_video(viewer.kid_id, video_id)
    kid_channels = channels_svc.visible_to_kid(viewer.kid_id)
    bookmarked_ids = kid_lib.channels_for_video(viewer.kid_id, video_id)
    return render_kid_template(request, viewer, "kid/video_edit.html", {
        "video": video,
        "overrides": overrides,
        "tags": tags,
        "kid_channels": kid_channels,
        "bookmarked_channel_ids": bookmarked_ids,
    })


@router.post("/video/{video_id}/edit")
async def video_edit_save(request: Request, video_id: str,
                          viewer: ViewerContext = Depends(_customize),
                          content: ContentService = Depends(get_content_service),
                          kid_lib: KidLibraryService = Depends(get_kid_library_service),
                          channels_svc: ChannelService = Depends(get_channel_service),
                          events: EventService = Depends(get_event_service)):
    content.get_video_for_viewer(video_id, viewer)
    form = await request.form()

    title = form_str(form, "title").strip()
    kid_lib.set_title(viewer.kid_id, video_id, title)

    description = form_str(form, "description").strip()
    kid_lib.set_description(viewer.kid_id, video_id, description)

    thumbnail = form.get("thumbnail")
    if isinstance(thumbnail, StarletteUploadFile):
        data = await thumbnail.read()
        if data:
            try:
                kid_lib.upload_thumbnail(
                    viewer.kid_id, video_id, data, thumbnail.filename or "")
            except ValueError:
                pass

    if form.get("clear_thumbnail"):
        kid_lib.clear_thumbnail(viewer.kid_id, video_id)

    tags_raw = form_str(form, "tags").strip()
    tag_names = [t.strip() for t in tags_raw.split(",") if t.strip()]
    kid_lib.sync_tags(viewer.kid_id, video_id, tag_names)

    kid_channels = channels_svc.visible_to_kid(viewer.kid_id)
    owned = [c for c in kid_channels
             if c.id is not None and c.owner_profile_id == viewer.profile_id]
    for ch_id in [c.id for c in owned if c.id is not None]:
        if form.get(f"channel_{ch_id}"):
            kid_lib.bookmark_video(viewer.kid_id, ch_id, video_id)
        else:
            kid_lib.unbookmark_video(viewer.kid_id, ch_id, video_id)

    # A new channel of the kid's own, with this video in it.
    new_name = " ".join(form_str(form, "new_channel").split())
    if new_name:
        same = next((c for c in owned if c.name.casefold() == new_name.casefold()), None)
        try:
            channel = same or channels_svc.create_for_kid(new_name, owner_profile_id=viewer.kid_id)
        except ValueError as e:
            response = render_kid_template(request, viewer, "kid/video_edit.html", {
                "video": content.get_video_for_viewer(video_id, viewer),
                "overrides": kid_lib.get_overrides(viewer.kid_id, video_id),
                "tags": kid_lib.tags_for_video(viewer.kid_id, video_id),
                "kid_channels": kid_channels,
                "bookmarked_channel_ids": kid_lib.channels_for_video(viewer.kid_id, video_id),
                "channel_error": str(e), "new_channel": new_name,
            })
            response.status_code = 400
            return response
        if same is None:
            events.log("channel_created", profile_id=viewer.profile_id,
                       data={"channel_id": channel.id, "name": channel.name})
        if channel.id is not None:
            kid_lib.bookmark_video(viewer.kid_id, channel.id, video_id)

    return RedirectResponse(url=f"/watch/{video_id}", status_code=302)


@router.post("/video/{video_id}/reset")
def video_reset_field(request: Request, video_id: str,
                      field: str = Form(...),
                      viewer: ViewerContext = Depends(_customize),
                      content: ContentService = Depends(get_content_service),
                      kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    """Roll back one override field (title / description / thumbnail)
    without touching the others."""
    content.get_video_for_viewer(video_id, viewer)
    if field == "title":
        kid_lib.clear_title(viewer.kid_id, video_id)
    elif field == "description":
        kid_lib.clear_description(viewer.kid_id, video_id)
    elif field == "thumbnail":
        kid_lib.clear_thumbnail(viewer.kid_id, video_id)
    return RedirectResponse(
        url=f"/video/{video_id}/edit", status_code=302)


@router.post("/video/{video_id}/tags/add")
def video_tag_add(request: Request, video_id: str,
                  tag: str = Form(""),
                  viewer: ViewerContext = Depends(_tags),
                  content: ContentService = Depends(get_content_service),
                  kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    content.get_video_for_viewer(video_id, viewer)
    tag = tag.strip()
    tag_id = kid_lib.add_tag(viewer.kid_id, video_id, tag) if tag else None
    if is_xhr(request):
        if tag_id is None:
            return Response(status_code=204)
        return render_kid_template(request, viewer, "kid/_tag_chip_response.html", {
            "tag": {"id": tag_id, "name": tag}, "video_id": video_id})
    return RedirectResponse(url=f"/watch/{video_id}#tags", status_code=302)


@router.post("/video/{video_id}/tags/remove")
def video_tag_remove(request: Request, video_id: str,
                     tag_id: int = Form(...),
                     viewer: ViewerContext = Depends(_tags),
                     content: ContentService = Depends(get_content_service),
                     kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    content.get_video_for_viewer(video_id, viewer)
    kid_lib.remove_tag(viewer.kid_id, video_id, tag_id)
    if is_xhr(request):
        return Response(status_code=204)
    return RedirectResponse(url=f"/watch/{video_id}#tags", status_code=302)


# --- Channel edit ---

@router.get("/channel/{channel_id}/edit", response_class=HTMLResponse)
def channel_edit_page(request: Request, channel_id: int,
                      viewer: ViewerContext = Depends(_customize),
                      channels_svc: ChannelService = Depends(get_channel_service)):
    channel = channels_svc.get(channel_id)
    if not channel or channel.owner_profile_id != viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)
    return render_kid_template(request, viewer, "kid/channel_edit.html", {
        "channel": channel,
    })


def _own_channel(channels_svc: ChannelService, viewer: ViewerContext, channel_id: int):
    channel = channels_svc.get(channel_id)
    if channel is None or channel.owner_profile_id != viewer.profile_id:
        return None
    return channel


@router.get("/channel/{channel_id}/delete", response_class=HTMLResponse)
def channel_delete_page(request: Request, channel_id: int,
                        viewer: ViewerContext = Depends(_customize),
                        channels_svc: ChannelService = Depends(get_channel_service),
                        content: ContentService = Depends(get_content_service)):
    """Are you sure? A page, not a dialog, so it reads the same on every
    tablet."""
    channel = _own_channel(channels_svc, viewer, channel_id)
    if channel is None:
        return RedirectResponse(url="/", status_code=302)
    return render_kid_template(request, viewer, "kid/channel_delete.html", {
        "channel": channel,
        "uploads": content.video_repo.count(status=None, channel_id=channel_id),
    })


@router.post("/channel/{channel_id}/delete")
def channel_delete(request: Request, channel_id: int,
                   viewer: ViewerContext = Depends(_customize),
                   channels_svc: ChannelService = Depends(get_channel_service),
                   events: EventService = Depends(get_event_service)):
    """Only the kid who made it. Videos saved into it just leave it;
    videos uploaded into it stay in the library with no channel, for
    the parent to place."""
    channel = _own_channel(channels_svc, viewer, channel_id)
    if channel is not None:
        channels_svc.delete(channel_id)
        events.log("channel_deleted", profile_id=viewer.profile_id,
                   data={"channel_id": channel_id, "name": channel.name})
    return RedirectResponse(url="/", status_code=302)


@router.post("/channel/{channel_id}/edit")
async def channel_edit_save(request: Request, channel_id: int,
                            viewer: ViewerContext = Depends(_customize),
                            channels_svc: ChannelService = Depends(get_channel_service)):
    channel = channels_svc.get(channel_id)
    if not channel or channel.owner_profile_id != viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    name = " ".join(form_str(form, "name").split())[:64] or channel.name
    if channels_svc.name_taken(name, channel_id):
        response = render_kid_template(request, viewer, "kid/channel_edit.html", {
            "channel": channel, "error": "That name is already used. Try another one.",
            "typed_name": name,
        })
        response.status_code = 400
        return response
    description = form_str(form, "description").strip()[:1000]
    color = form_str(form, "color").strip()
    if not valid_color(color):
        color = channel.color

    updates = {"name": name, "description": description, "color": color}

    for kind in ASSET_KINDS:
        upload = form.get(kind)
        if upload and hasattr(upload, "read"):
            data = await upload.read()
            if data:
                try:
                    updates[f"{kind}_filename"] = channels_svc.save_asset(
                        channel_id, kind, data)
                except ValueError:
                    pass

    channels_svc.update(channel_id, **updates)
    return RedirectResponse(url=f"/channel/{channel_id}", status_code=302)


# --- Tags ---

@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request,
              viewer: ViewerContext = Depends(_tags),
              content: ContentService = Depends(get_content_service),
              kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    if not viewer.is_child or not viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)
    visible_ids = content.visible_channel_ids(viewer) or []
    cloud = kid_lib.tag_cloud(viewer.profile_id, visible_ids)
    return render_kid_template(request, viewer, "kid/tags.html", {
        "tag_cloud": cloud,
    })


@router.get("/tags/{tag_name}", response_class=HTMLResponse)
def tag_videos(request: Request, tag_name: str,
               page: int = Query(1, ge=1),
               viewer: ViewerContext = Depends(_tags),
               content: ContentService = Depends(get_content_service),
               kid_lib: KidLibraryService = Depends(get_kid_library_service),
               reactions: ReactionService = Depends(get_reaction_service)):
    if not viewer.is_child or not viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)
    visible_ids = content.visible_channel_ids(viewer) or []
    video_ids, total = kid_lib.videos_by_tag(
        viewer.profile_id, tag_name, visible_ids, page, ITEMS_PER_PAGE)
    videos = [content.get_video(vid) for vid in video_ids]
    videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    bulk_counts = reactions.get_bulk_counts(video_ids) if video_ids else {}
    return render_kid_template(request, viewer, "kid/home.html", {
        "videos": videos,
        "channels": content.channels_to_browse(viewer),
        "page": page,
        "total_pages": total_pages,
        "reaction_counts": bulk_counts,
        "emojis": dict(reactions.get_emoji_list()),
        "current_channel_id": None,
        "tag_name": tag_name,
    })
