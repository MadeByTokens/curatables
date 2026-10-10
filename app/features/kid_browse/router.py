"""Kid browsing: home grid with pagination and channel filtering."""

from fastapi import APIRouter, Depends, Form, Query, Request
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_viewer, get_content_service, get_profile_service,
    get_reaction_service, get_kid_library_service, get_channel_service,
    require_kid_feature,
)
from app.services.content import ContentService, VideoNotFound
from app.services.profiles import ProfileService
from app.services.reactions import ReactionService
from app.services.kid_library import KidLibraryService
from app.services.channels import ASSET_KINDS, ChannelService, valid_color
from app.web import form_str, is_xhr
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-browse"])

ITEMS_PER_PAGE = 24

_customize = require_kid_feature("allow_customize")
_tags = require_kid_feature("allow_tags")


def _build_home_context(videos, content, reactions, viewer, page, total,
                        current_channel_id=None):
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    channels = content.channels_for_viewer(viewer)
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


@router.get("/", response_class=HTMLResponse)
def home(request: Request,
         page: int = Query(1, ge=1),
         viewer: ViewerContext = Depends(get_viewer),
         content: ContentService = Depends(get_content_service),
         profiles_svc: ProfileService = Depends(get_profile_service),
         reactions: ReactionService = Depends(get_reaction_service),
         kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    if viewer.viewer_type == "anonymous":
        profiles = profiles_svc.list()
        if len(profiles) == 1 and not profiles[0].pin:
            request.session["profile_id"] = profiles[0].id
            return RedirectResponse(url="/", status_code=302)
        return RedirectResponse(url="/profiles", status_code=302)

    videos, total = content.list_for_viewer(viewer, page=page, per_page=ITEMS_PER_PAGE)
    if viewer.is_child and viewer.profile_id:
        videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    ctx = _build_home_context(videos, content, reactions, viewer, page, total)
    return render_kid_template(request, viewer, "kid/home.html", ctx)


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
                          channels_svc: ChannelService = Depends(get_channel_service)):
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
    owned_ids = [c.id for c in kid_channels
                 if c.id is not None and c.owner_profile_id == viewer.profile_id]
    for ch_id in owned_ids:
        if form.get(f"channel_{ch_id}"):
            kid_lib.bookmark_video(viewer.kid_id, ch_id, video_id)
        else:
            kid_lib.unbookmark_video(viewer.kid_id, ch_id, video_id)

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


@router.post("/channel/{channel_id}/edit")
async def channel_edit_save(request: Request, channel_id: int,
                            viewer: ViewerContext = Depends(_customize),
                            channels_svc: ChannelService = Depends(get_channel_service)):
    channel = channels_svc.get(channel_id)
    if not channel or channel.owner_profile_id != viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    name = form_str(form, "name").strip()[:64] or channel.name
    if channels_svc.name_taken(name, channel_id):
        name = channel.name
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
        "channels": content.channels_for_viewer(viewer),
        "page": page,
        "total_pages": total_pages,
        "reaction_counts": bulk_counts,
        "emojis": dict(reactions.get_emoji_list()),
        "current_channel_id": None,
        "tag_name": tag_name,
    })
