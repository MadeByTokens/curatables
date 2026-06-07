"""Kid browsing: home grid with pagination and channel filtering."""

from fastapi import APIRouter, Depends, Query, Request
from starlette.responses import (
    HTMLResponse, RedirectResponse, JSONResponse, Response,
)

from fastapi import Form

from app.dependencies import (
    get_viewer, get_content_service, get_profile_service,
    get_reaction_service, get_kid_library_service, get_channel_service,
    get_config, require_child,
)
from app.services.content import ContentService
from app.services.profiles import ProfileService
from app.services.reactions import ReactionService
from app.services.kid_library import KidLibraryService
from app.services.channels import ChannelService
from app.models import ViewerContext
from app.template_utils import render_kid_template

router = APIRouter(tags=["kid-browse"])

ITEMS_PER_PAGE = 24


def _build_home_context(videos, content, reactions, viewer, page, total,
                        current_channel_id=None):
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    channels = content.list_channels()
    video_ids = [v.video_id for v in videos]
    bulk_counts = reactions.get_bulk_counts(video_ids) if video_ids else {}
    return {
        "videos": videos,
        "channels": channels,
        "page": page,
        "total_pages": total_pages,
        "reaction_counts": bulk_counts,
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
        if profiles:
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


@router.get("/channel/{channel_id}", response_class=HTMLResponse)
def channel_view(request: Request, channel_id: int,
                 page: int = Query(1, ge=1),
                 viewer: ViewerContext = Depends(get_viewer),
                 content: ContentService = Depends(get_content_service),
                 reactions: ReactionService = Depends(get_reaction_service),
                 kid_lib: KidLibraryService = Depends(get_kid_library_service),
                 channels_svc: ChannelService = Depends(get_channel_service)):
    channel = channels_svc.get(channel_id)
    videos, total = content.list_for_viewer(
        viewer, page=page, per_page=ITEMS_PER_PAGE, channel_id=channel_id)
    if viewer.is_child and viewer.profile_id:
        videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    ctx = _build_home_context(videos, content, reactions, viewer, page, total,
                              current_channel_id=channel_id)
    ctx["channel"] = channel
    return render_kid_template(request, viewer, "kid/home.html", ctx)


# --- Video customize ---

@router.get("/video/{video_id}/edit", response_class=HTMLResponse)
def video_edit_page(request: Request, video_id: str,
                    viewer: ViewerContext = Depends(require_child),
                    content: ContentService = Depends(get_content_service),
                    kid_lib: KidLibraryService = Depends(get_kid_library_service),
                    channels_svc: ChannelService = Depends(get_channel_service)):
    video = content.get_video_for_viewer(video_id, viewer)
    overrides = kid_lib.get_overrides(viewer.profile_id, video_id)
    tags = kid_lib.tags_for_video(viewer.profile_id, video_id)
    kid_channels = channels_svc.visible_to_kid(
        viewer.profile_id, viewer.allowed_channel_ids)
    bookmarked_ids = kid_lib.channels_for_video(viewer.profile_id, video_id)
    return render_kid_template(request, viewer, "kid/video_edit.html", {
        "video": video,
        "overrides": overrides,
        "tags": tags,
        "kid_channels": kid_channels,
        "bookmarked_channel_ids": bookmarked_ids,
    })


@router.post("/video/{video_id}/edit")
async def video_edit_save(request: Request, video_id: str,
                          viewer: ViewerContext = Depends(require_child),
                          content: ContentService = Depends(get_content_service),
                          kid_lib: KidLibraryService = Depends(get_kid_library_service),
                          channels_svc: ChannelService = Depends(get_channel_service)):
    content.get_video_for_viewer(video_id, viewer)
    form = await request.form()

    title = (form.get("title") or "").strip()
    kid_lib.set_title(viewer.profile_id, video_id, title)

    description = (form.get("description") or "").strip()
    kid_lib.set_description(viewer.profile_id, video_id, description)

    thumbnail = form.get("thumbnail")
    if thumbnail and hasattr(thumbnail, "read"):
        data = await thumbnail.read()
        if data and len(data) > 0:
            kid_lib.upload_thumbnail(
                viewer.profile_id, video_id, data, thumbnail.filename)

    if form.get("clear_thumbnail"):
        kid_lib.clear_thumbnail(viewer.profile_id, video_id)

    tags_raw = (form.get("tags") or "").strip()
    tag_names = [t.strip() for t in tags_raw.split(",") if t.strip()]
    kid_lib.sync_tags(viewer.profile_id, video_id, tag_names)

    kid_channels = channels_svc.visible_to_kid(
        viewer.profile_id, viewer.allowed_channel_ids)
    owned_ids = [c.id for c in kid_channels if c.owner_profile_id == viewer.profile_id]
    for ch_id in owned_ids:
        if form.get(f"channel_{ch_id}"):
            kid_lib.bookmark_video(viewer.profile_id, ch_id, video_id)
        else:
            kid_lib.unbookmark_video(viewer.profile_id, ch_id, video_id)

    return RedirectResponse(url=f"/watch/{video_id}", status_code=302)


@router.post("/video/{video_id}/reset")
def video_reset_field(request: Request, video_id: str,
                      field: str = Form(...),
                      viewer: ViewerContext = Depends(require_child),
                      content: ContentService = Depends(get_content_service),
                      kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    """Roll back one override field (title / description / thumbnail)
    without touching the others."""
    content.get_video_for_viewer(video_id, viewer)
    if field == "title":
        kid_lib.clear_title(viewer.profile_id, video_id)
    elif field == "description":
        kid_lib.clear_description(viewer.profile_id, video_id)
    elif field == "thumbnail":
        kid_lib.clear_thumbnail(viewer.profile_id, video_id)
    return RedirectResponse(
        url=f"/video/{video_id}/edit", status_code=302)


def _is_xhr(request: Request) -> bool:
    """The watch page's inline tag editor calls these endpoints via XHR
    (X-Requested-With) so it can update the chips in place — a full
    redirect/reload would re-create the <video> element and restart
    playback. A plain <form> submit (JS off / old browser) has no such
    header and still gets the PRG redirect below."""
    return request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"


@router.post("/video/{video_id}/tags/add")
def video_tag_add(request: Request, video_id: str,
                  tag: str = Form(""),
                  viewer: ViewerContext = Depends(require_child),
                  content: ContentService = Depends(get_content_service),
                  kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    content.get_video_for_viewer(video_id, viewer)
    tag = tag.strip()
    tag_id = kid_lib.add_tag(viewer.profile_id, video_id, tag) if tag else None
    if _is_xhr(request):
        if tag_id is None:
            return Response(status_code=204)
        return JSONResponse({"id": tag_id, "name": tag})
    return RedirectResponse(url=f"/watch/{video_id}#tags", status_code=302)


@router.post("/video/{video_id}/tags/remove")
def video_tag_remove(request: Request, video_id: str,
                     tag_id: int = Form(...),
                     viewer: ViewerContext = Depends(require_child),
                     content: ContentService = Depends(get_content_service),
                     kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    content.get_video_for_viewer(video_id, viewer)
    kid_lib.remove_tag(viewer.profile_id, video_id, tag_id)
    if _is_xhr(request):
        return Response(status_code=204)
    return RedirectResponse(url=f"/watch/{video_id}#tags", status_code=302)


# --- Channel edit ---

@router.get("/channel/{channel_id}/edit", response_class=HTMLResponse)
def channel_edit_page(request: Request, channel_id: int,
                      viewer: ViewerContext = Depends(require_child),
                      channels_svc: ChannelService = Depends(get_channel_service)):
    channel = channels_svc.get(channel_id)
    if not channel or channel.owner_profile_id != viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)
    return render_kid_template(request, viewer, "kid/channel_edit.html", {
        "channel": channel,
    })


@router.post("/channel/{channel_id}/edit")
async def channel_edit_save(request: Request, channel_id: int,
                            viewer: ViewerContext = Depends(require_child),
                            channels_svc: ChannelService = Depends(get_channel_service),
                            config=Depends(get_config)):
    channel = channels_svc.get(channel_id)
    if not channel or channel.owner_profile_id != viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)

    form = await request.form()
    name = (form.get("name") or "").strip() or channel.name
    description = (form.get("description") or "").strip()
    color = (form.get("color") or "").strip() or channel.color

    updates = {"name": name, "description": description, "color": color}

    channel_dir = config.data_dir / "channels" / str(channel_id)

    banner = form.get("banner")
    if banner and hasattr(banner, "read"):
        data = await banner.read()
        if data:
            import os
            ext = os.path.splitext(banner.filename)[1].lower() or ".jpg"
            channel_dir.mkdir(parents=True, exist_ok=True)
            for old in channel_dir.glob("banner.*"):
                old.unlink(missing_ok=True)
            (channel_dir / f"banner{ext}").write_bytes(data)
            updates["banner_filename"] = f"banner{ext}"

    icon = form.get("icon")
    if icon and hasattr(icon, "read"):
        data = await icon.read()
        if data:
            import os
            ext = os.path.splitext(icon.filename)[1].lower() or ".jpg"
            channel_dir.mkdir(parents=True, exist_ok=True)
            for old in channel_dir.glob("icon.*"):
                old.unlink(missing_ok=True)
            (channel_dir / f"icon{ext}").write_bytes(data)
            updates["icon_filename"] = f"icon{ext}"

    channels_svc.update(channel_id, **updates)
    return RedirectResponse(url=f"/channel/{channel_id}", status_code=302)


# --- Tags ---

@router.get("/tags", response_class=HTMLResponse)
def tags_page(request: Request,
              viewer: ViewerContext = Depends(get_viewer),
              content: ContentService = Depends(get_content_service),
              kid_lib: KidLibraryService = Depends(get_kid_library_service)):
    if not viewer.is_child or not viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)
    visible_ids = content._kid_visible_channel_ids(viewer)
    cloud = kid_lib.tag_cloud(viewer.profile_id, visible_ids)
    return render_kid_template(request, viewer, "kid/tags.html", {
        "tag_cloud": cloud,
    })


@router.get("/tags/{tag_name}", response_class=HTMLResponse)
def tag_videos(request: Request, tag_name: str,
               page: int = Query(1, ge=1),
               viewer: ViewerContext = Depends(get_viewer),
               content: ContentService = Depends(get_content_service),
               kid_lib: KidLibraryService = Depends(get_kid_library_service),
               reactions: ReactionService = Depends(get_reaction_service)):
    if not viewer.is_child or not viewer.profile_id:
        return RedirectResponse(url="/", status_code=302)
    visible_ids = content._kid_visible_channel_ids(viewer)
    video_ids, total = kid_lib.videos_by_tag(
        viewer.profile_id, tag_name, visible_ids, page, ITEMS_PER_PAGE)
    videos = [content.get_video(vid) for vid in video_ids]
    videos = kid_lib.apply_overrides(viewer.profile_id, videos)
    total_pages = max(1, (total + ITEMS_PER_PAGE - 1) // ITEMS_PER_PAGE)
    bulk_counts = reactions.get_bulk_counts(video_ids) if video_ids else {}
    return render_kid_template(request, viewer, "kid/home.html", {
        "videos": videos,
        "channels": content.list_channels(),
        "page": page,
        "total_pages": total_pages,
        "reaction_counts": bulk_counts,
        "current_channel_id": None,
        "tag_name": tag_name,
    })
