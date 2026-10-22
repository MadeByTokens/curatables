"""Kid browsing: home grid with pagination and channel filtering."""

import re

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_viewer, get_content_service, get_profile_service,
    get_reaction_service, get_kid_library_service, get_channel_service,
    get_event_service, get_kid_home_service, get_channel_order_service,
    require_child, require_kid_feature,
)
from app.services.content import ContentService
from app.services.events import EventService
from app.services.profiles import ProfileService
from app.services.reactions import ReactionService
from app.services import guides
from app.services.channel_order import ALL_SORTS, SORTS as CHANNEL_SORTS, START_SORTS, ChannelOrderService
from app.services.kid_home import SLOTS, KidHomeService
from app.services.kid_library import KidLibraryService
from app.services.channels import ASSET_KINDS, ChannelService, valid_color
from app.constants import CHANNEL_EMOJIS
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
               kid_lib: KidLibraryService = Depends(get_kid_library_service),
               order: ChannelOrderService = Depends(get_channel_order_service)):
    return _all_videos(request, page, viewer, content, reactions, kid_lib, order)


def _all_videos(request, page, viewer, content, reactions, kid_lib, order=None):
    if order is not None and viewer.is_child:
        videos, total = order.page_all(viewer, page, ITEMS_PER_PAGE)
    else:
        videos, total = content.list_for_viewer(viewer, page=page, per_page=ITEMS_PER_PAGE)
    if viewer.is_child and viewer.profile_id:
        videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    ctx = _build_home_context(videos, content, reactions, viewer, page, total)
    if order is not None and viewer.is_child:
        ctx["sort"] = order.sort_for_all(viewer)
        ctx["sorts"] = ALL_SORTS
        ctx["sort_action"] = "/all/sort"
    return render_kid_template(request, viewer, "kid/home.html", ctx)


@router.post("/all/sort")
async def all_videos_sort(request: Request,
                          viewer: ViewerContext = Depends(require_child),
                          order: ChannelOrderService = Depends(get_channel_order_service)):
    """Keep the order the kid picked for All videos, then show it."""
    form = await request.form()
    order.choose_all(viewer, form_str(form, "sort"))
    return RedirectResponse(url="/all", status_code=303)


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
    return render_kid_template(request, viewer, "kid/help.html", {
        "has_guide": bool(guides.tours("kids", viewer.language or "en", viewer)[1]),
    })


@router.get("/help/guide", response_class=HTMLResponse)
def guide_list(request: Request, viewer: ViewerContext = Depends(get_viewer)):
    _lang, tours = guides.tours("kids", viewer.language or "en", viewer)
    return render_kid_template(request, viewer, "kid/guide.html", {"tours": tours})


@router.get("/help/guide/{tour_id}", response_class=HTMLResponse)
def guide_tour(request: Request, tour_id: str, viewer: ViewerContext = Depends(get_viewer)):
    lang, tour = guides.tour("kids", viewer.language or "en", tour_id, viewer)
    if tour is None:
        return RedirectResponse(url="/help/guide", status_code=302)
    return render_kid_template(request, viewer, "kid/guide_tour.html", {
        "tour": tour, "lang": lang, "finger": guides.finger})


# --- The Channels page ---

@router.get("/channels", response_class=HTMLResponse)
def channels_page(request: Request,
                  viewer: ViewerContext = Depends(get_viewer),
                  content: ContentService = Depends(get_content_service)):
    """Every channel the kid can watch, as big cards with the picture and
    name the kid gave them, even when there is only one."""
    return render_kid_template(request, viewer, "kid/channels.html", {
        "channels": content.channels_to_browse(viewer),
        "nav_active": "channels",
    })


@router.get("/channels/new", response_class=HTMLResponse)
def channel_new_page(request: Request, viewer: ViewerContext = Depends(_customize)):
    return render_kid_template(request, viewer, "kid/channel_new.html", {"nav_active": "channels"})


@router.post("/channels/new")
def channel_new(request: Request,
                name: str = Form(""),
                viewer: ViewerContext = Depends(_customize),
                channels_svc: ChannelService = Depends(get_channel_service),
                events: EventService = Depends(get_event_service)):
    """A channel of the kid's own, made from the Channels page."""
    try:
        channel = channels_svc.create_for_kid(" ".join(name.split()), owner_profile_id=viewer.kid_id)
    except ValueError as e:
        response = render_kid_template(request, viewer, "kid/channel_new.html", {
            "nav_active": "channels", "error": str(e), "typed_name": name[:64]})
        response.status_code = 400
        return response
    events.log("channel_created", profile_id=viewer.profile_id,
               data={"channel_id": channel.id, "name": channel.name})
    return RedirectResponse(url="/channels", status_code=303)


def _look_page(request, viewer, channel, look, error=None):
    response = render_kid_template(request, viewer, "kid/channel_look.html", {
        "channel": channel, "look": look, "emojis": CHANNEL_EMOJIS, "error": error,
        "nav_active": "channels",
    })
    if error:
        response.status_code = 400
    return response


def _not_own(content: ContentService, viewer: ViewerContext, channel_id: int):
    """A channel the kid sees and didn't make (their own has Edit Channel)."""
    channel = content.channel_for_viewer(channel_id, viewer)
    return channel if channel is not None and channel.owner_profile_id != viewer.profile_id else None


@router.get("/channels/{channel_id}/look", response_class=HTMLResponse)
def channel_look_page(request: Request, channel_id: int,
                      viewer: ViewerContext = Depends(_customize),
                      content: ContentService = Depends(get_content_service)):
    channel = _not_own(content, viewer, channel_id)
    if channel is None:
        return RedirectResponse(url="/channels", status_code=302)
    return _look_page(request, viewer, channel, content.channel_repo.look(viewer.kid_id, channel_id))


@router.post("/channels/{channel_id}/look")
async def channel_look_save(request: Request, channel_id: int,
                            viewer: ViewerContext = Depends(_customize),
                            content: ContentService = Depends(get_content_service),
                            channels_svc: ChannelService = Depends(get_channel_service)):
    """The kid's own name and picture for a channel; only they see it."""
    channel = _not_own(content, viewer, channel_id)
    if channel is None:
        return RedirectResponse(url="/channels", status_code=302)
    form = await request.form()
    look = content.channel_repo.look(viewer.kid_id, channel_id)
    name = " ".join(form_str(form, "name").split())[:64]
    original = channel.original_name or channel.name
    look["name"] = "" if name == original else name
    emoji = form_str(form, "emoji")
    if emoji in CHANNEL_EMOJIS:
        look["emoji"] = emoji
    upload = form.get("picture")
    if isinstance(upload, StarletteUploadFile):
        data = await upload.read()
        if data:
            try:
                look["picture"] = channels_svc.save_kid_picture(channel_id, viewer.kid_id, data)
                look["emoji"] = ""
            except ValueError:
                return _look_page(request, viewer, channel, look,
                                  error="That picture didn't work. Try another one.")
    elif emoji in CHANNEL_EMOJIS and look["picture"]:
        # The form ticks no emoji while a photo is in use, so one sent
        # was tapped: it replaces the photo.
        channels_svc.remove_kid_picture(channel_id, viewer.kid_id)
        look["picture"] = ""
    content.channel_repo.set_look(viewer.kid_id, channel_id, look["name"], look["emoji"], look["picture"])
    return RedirectResponse(url="/channels", status_code=303)


@router.post("/channels/{channel_id}/look/reset")
def channel_look_reset(channel_id: int,
                       viewer: ViewerContext = Depends(_customize),
                       content: ContentService = Depends(get_content_service),
                       channels_svc: ChannelService = Depends(get_channel_service)):
    if _not_own(content, viewer, channel_id) is not None:
        channels_svc.remove_kid_picture(channel_id, viewer.kid_id)
        content.channel_repo.set_look(viewer.kid_id, channel_id, "", "", "")
    return RedirectResponse(url="/channels", status_code=303)


@router.get("/channel/{channel_id}", response_class=HTMLResponse)
def channel_view(request: Request, channel_id: int,
                 page: int = Query(1, ge=1),
                 done: str = Query(""),
                 viewer: ViewerContext = Depends(get_viewer),
                 content: ContentService = Depends(get_content_service),
                 reactions: ReactionService = Depends(get_reaction_service),
                 kid_lib: KidLibraryService = Depends(get_kid_library_service),
                 channels_svc: ChannelService = Depends(get_channel_service),
                 order: ChannelOrderService = Depends(get_channel_order_service)):
    channel = channels_svc.get(channel_id)
    visible = content.visible_channel_ids(viewer)
    if channel is None or (visible is not None and channel_id not in visible):
        return RedirectResponse(url="/", status_code=302)
    videos, total = order.page(viewer, channel, page, ITEMS_PER_PAGE)
    if viewer.is_child and viewer.profile_id:
        videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    ctx = _build_home_context(videos, content, reactions, viewer, page, total,
                              current_channel_id=channel_id)
    ctx["channel"] = content.channel_for_viewer(channel_id, viewer) or channel
    ctx["sort"] = order.sort_for(viewer, channel_id)
    ctx["sorts"] = CHANNEL_SORTS if viewer.is_child else ()
    ctx["sort_action"] = f"/channel/{channel_id}/sort"
    if viewer.is_child and ctx["sort"] == "mine":
        ctx["my_order_saved"] = order.my_order(viewer, channel_id)["saved"] is not None
        ctx["can_undo_save"] = order.can_undo_save(viewer, channel_id)
    ctx["order_done"] = done if done in ("saved", "unsaved", "cancelled") else ""
    return render_kid_template(request, viewer, "kid/home.html", ctx)


# --- My order: arranging a channel ---

ARRANGE_PER_PAGE = 30


def _seen_channel(channel_id: int, viewer: ViewerContext, content: ContentService,
                  channels_svc: ChannelService):
    channel = channels_svc.get(channel_id)
    visible = content.visible_channel_ids(viewer)
    if channel is None or (visible is not None and channel_id not in visible):
        return None
    return channel


@router.get("/channel/{channel_id}/arrange", response_class=HTMLResponse)
def channel_arrange(request: Request, channel_id: int,
                    page: int = Query(1, ge=1),
                    ask: str = Query(""),
                    moved: str = Query(""),
                    place: int = Query(0),
                    viewer: ViewerContext = Depends(require_child),
                    content: ContentService = Depends(get_content_service),
                    kid_lib: KidLibraryService = Depends(get_kid_library_service),
                    channels_svc: ChannelService = Depends(get_channel_service),
                    order: ChannelOrderService = Depends(get_channel_order_service)):
    """Arranging My order: a compact list of the draft, page by page."""
    channel = _seen_channel(channel_id, viewer, content, channels_svc)
    if channel is None:
        return RedirectResponse(url="/channels", status_code=302)
    draft = order.draft(viewer, channel)
    pages = max(1, (len(draft) + ARRANGE_PER_PAGE - 1) // ARRANGE_PER_PAGE)
    page = min(page, pages)
    start = (page - 1) * ARRANGE_PER_PAGE
    shown = kid_lib.apply_overrides(viewer.kid_id, draft[start:start + ARRANGE_PER_PAGE])
    return render_kid_template(request, viewer, "kid/channel_arrange.html", {
        "channel": content.channel_for_viewer(channel_id, viewer) or channel,
        "rows": [(start + n + 1, v) for n, v in enumerate(shown)],
        "total": len(draft), "page": page, "pages": pages,
        "starts": START_SORTS,
        "ask": ask if ask in START_SORTS else "",
        "can_undo": order.has_moves(viewer, channel_id),
        "moved": moved, "moved_place": place,
        "nav_active": "channels",
    })


@router.post("/channel/{channel_id}/arrange")
async def channel_arrange_do(request: Request, channel_id: int,
                             viewer: ViewerContext = Depends(require_child),
                             content: ContentService = Depends(get_content_service),
                             channels_svc: ChannelService = Depends(get_channel_service),
                             order: ChannelOrderService = Depends(get_channel_order_service)):
    """One change to the draft (a move, Start from, Undo), or Save or
    Cancel. Every change is one plain form, so it works on old tablets."""
    channel = _seen_channel(channel_id, viewer, content, channels_svc)
    if channel is None:
        return RedirectResponse(url="/channels", status_code=303)
    form = await request.form()
    do = form_str(form, "do")
    page = form_str(form, "page")
    here = f"/channel/{channel_id}/arrange?page={page if page.isdigit() else 1}"
    if do == "save":
        order.save(viewer, channel)
        return RedirectResponse(url=f"/channel/{channel_id}?done=saved", status_code=303)
    if do == "cancel":
        order.cancel(viewer, channel)
        return RedirectResponse(url=f"/channel/{channel_id}?done=cancelled", status_code=303)
    if do == "undo":
        order.undo(viewer, channel)
        return RedirectResponse(url=here, status_code=303)
    if do == "start":
        sort = form_str(form, "sort")
        # Moves already made are not wiped by one tap: ask first.
        if order.has_moves(viewer, channel_id) and form_str(form, "sure") != "1":
            return RedirectResponse(url=f"{here}&ask={sort}", status_code=303)
        order.start_from(viewer, channel, sort)
        return RedirectResponse(url=here, status_code=303)
    video_id = form_str(form, "video_id")
    if do in ("up", "down", "top", "end", "place"):
        place = form_str(form, "place").strip()
        if do == "place" and not place.isdigit():
            return RedirectResponse(url=here, status_code=303)
        new_place = order.move(viewer, channel, video_id, do, int(place) if place.isdigit() else 0)
        if new_place is not None:
            return RedirectResponse(url=f"{here}&moved={video_id}&place={new_place}#row-{video_id}",
                                    status_code=303)
    return RedirectResponse(url=here, status_code=303)


@router.post("/channel/{channel_id}/undo-save")
def channel_undo_save(channel_id: int,
                      viewer: ViewerContext = Depends(require_child),
                      content: ContentService = Depends(get_content_service),
                      channels_svc: ChannelService = Depends(get_channel_service),
                      order: ChannelOrderService = Depends(get_channel_order_service)):
    channel = _seen_channel(channel_id, viewer, content, channels_svc)
    if channel is None:
        return RedirectResponse(url="/channels", status_code=303)
    order.undo_save(viewer, channel)
    return RedirectResponse(url=f"/channel/{channel_id}?done=unsaved", status_code=303)


@router.post("/channel/{channel_id}/sort")
async def channel_sort(request: Request, channel_id: int,
                       viewer: ViewerContext = Depends(require_child),
                       content: ContentService = Depends(get_content_service),
                       channels_svc: ChannelService = Depends(get_channel_service),
                       order: ChannelOrderService = Depends(get_channel_order_service)):
    """Keep the order the kid picked for this channel, then show it."""
    form = await request.form()
    visible = content.visible_channel_ids(viewer)
    if channels_svc.get(channel_id) is not None and (visible is None or channel_id in visible):
        order.choose(viewer, channel_id, form_str(form, "sort"))
    return RedirectResponse(url=f"/channel/{channel_id}", status_code=303)


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
    video = content.get_video_for_viewer(video_id, viewer)
    form = await request.form()

    title = form_str(form, "title").strip()
    kid_lib.set_title(viewer.kid_id, video_id, title)

    # The box starts with the original text: left as it was, the video
    # keeps following the original. Emptied, it stays empty.
    description = form_str(form, "description").strip()
    if description == (video.description or "").strip():
        kid_lib.clear_description(viewer.kid_id, video_id)
    else:
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
