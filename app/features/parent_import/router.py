"""Parent: import video files that are already on a drive.

Browse the import folders, pick files, fill in a few details per file
(with an optional online lookup for a picture and a description), then
queue them. Jobs run in the background; this page follows them and can
cancel any of them at any time.
"""
from __future__ import annotations

import logging
import threading
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.config import Config
from app.dependencies import (
    get_channel_repo, get_config, get_import_job_repo, get_import_service,
    get_lookup_service, require_parent,
)
from app.models import ViewerContext
from app.repositories import ChannelRepository, VideoRepository
from app.repositories.import_job_repo import (
    ACTIVE_STATES, FINISHED_STATES, ImportJob, ImportJobRepository,
)
from app.features.parent_uploads.router import _ensure_default_channel
from app.services.images import MAX_IMAGE_BYTES, image_extension
from app.services.import_scan import ImportPathError, ProbeCache
from app.services.imports import ORIGINS, ImportFailed, ImportService
from app.services.ingest import IngestService
from app.services.lookup import LookupService
from app.services.media_probe import MediaProbeService
from app.services.storage import StorageService
from app.services.subtitles import language_label
from app.services.thumbnails import ThumbnailService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/parent/import", tags=["parent-import"])

PHASES = {"hash": "Checking the file", "copy": "Copying", "convert": "Converting",
          "subtitles": "Subtitles", "finish": "Finishing", "": "Starting"}
ORIGIN_LABELS = {"movie": "Movie or TV show (DVD, Blu-ray…)", "youtube": "YouTube",
                 "web": "Somewhere else on the internet", "home": "Home video"}
STALL_WARNING = 30


def _duration(seconds: float | None) -> str:
    if seconds is None:
        return ""
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    minutes = round(seconds / 60)
    if minutes < 60:
        return f"{minutes} min"
    return f"{minutes // 60} h {minutes % 60:02d} min"


def size_label(size: int) -> str:
    if size >= 1_073_741_824:
        return f"{size / 1_073_741_824:.1f} GB"
    return f"{max(1, round(size / 1_048_576))} MB"


def length_label(seconds: float | None) -> str:
    if not seconds:
        return ""
    total = int(round(seconds))
    hours, rest = divmod(total, 3600)
    return f"{hours}:{rest // 60:02d}:{rest % 60:02d}" if hours else f"{rest // 60}:{rest % 60:02d}"


def job_status(job: ImportJob) -> dict:
    """Everything the page shows about one job, as plain values."""
    fraction = job.fraction
    line = PHASES.get(job.phase, job.phase.title())
    if job.state == "queued":
        line = "Waiting its turn"
    elif job.state == "cancelling":
        line = "Cancelling…"
    elif job.state == "done":
        line = job.error or "Imported"
    elif job.state == "failed":
        line = job.error or "Failed"
    elif job.state == "cancelled":
        line = "Cancelled; nothing was changed"
    details = []
    if job.state == "working":
        if fraction is not None:
            details.append(f"{int(fraction * 100)}%")
        if job.phase == "convert" and job.speed:
            details.append(f"{job.speed:.1f}× real time")
        if job.eta_seconds is not None and job.phase in ("convert", "subtitles"):
            details.append(f"about {_duration(job.eta_seconds)} left")
        if job.stalled_for >= STALL_WARNING:
            details.append(f"no progress for {_duration(job.stalled_for)}")
    return {
        "id": job.id, "title": job.title, "name": job.name, "state": job.state,
        "phase": job.phase, "line": line, "details": " · ".join(details),
        "percent": int(fraction * 100) if fraction is not None else None,
        "stalled": job.state == "working" and job.stalled_for >= STALL_WARNING,
        "video_id": job.video_id, "active": job.state in ACTIVE_STATES,
        "mode": job.mode,
    }


def _templates(request: Request):
    return request.app.state.templates


def _worker(request: Request):
    return getattr(request.app.state, "import_worker", None)


def _prepare_in_background(request: Request, batch: str) -> None:
    connect = request.app.state.db_connect
    config = request.app.state.config

    def run() -> None:
        conn = connect()
        try:
            probe = MediaProbeService()
            video_repo = VideoRepository(conn)
            storage = StorageService(config.media_dir)
            service = ImportService(config, ImportJobRepository(conn), video_repo,
                                    ChannelRepository(conn),
                                    IngestService(storage, probe, video_repo,
                                                  ThumbnailService(config.media_dir)),
                                    ProbeCache(probe))
            service.prepare_batch(batch)
        except Exception:
            logger.exception("import: preparing batch %s failed", batch)
        finally:
            conn.close()

    threading.Thread(target=run, name=f"import-prepare-{batch}", daemon=True).start()


# -- home: the job list ------------------------------------------------------------

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
def import_home(request: Request, viewer: ViewerContext = Depends(require_parent),
                service: ImportService = Depends(get_import_service),
                repo: ImportJobRepository = Depends(get_import_job_repo)):
    active = repo.list_states(ACTIVE_STATES)
    finished = repo.list_states(FINISHED_STATES, limit=40)
    return _templates(request).TemplateResponse(request, "parent/import.html", {
        "request": request,
        "roots": service.roots.roots(),
        "problems": service.roots.problems(),
        "active": [job_status(j) for j in active],
        "finished": [job_status(j) for j in finished],
        "error": request.query_params.get("error", ""),
    })


@router.get("/status.json")
def import_status(viewer: ViewerContext = Depends(require_parent),
                  repo: ImportJobRepository = Depends(get_import_job_repo)):
    jobs = repo.list_states(ACTIVE_STATES) + repo.list_states(FINISHED_STATES, limit=40)
    return JSONResponse({"jobs": [job_status(j) for j in jobs],
                         "active": sum(1 for j in jobs if j.state in ACTIVE_STATES)})


@router.post("/jobs/{job_id}/cancel")
def cancel_job(request: Request, job_id: int,
               viewer: ViewerContext = Depends(require_parent),
               repo: ImportJobRepository = Depends(get_import_job_repo)):
    if repo.request_cancel(job_id) == "working" and _worker(request) is not None:
        _worker(request).cancel(job_id)
    return RedirectResponse(url="/parent/import", status_code=303)


@router.post("/cancel-all")
def cancel_all(request: Request, viewer: ViewerContext = Depends(require_parent),
               repo: ImportJobRepository = Depends(get_import_job_repo)):
    for job in repo.list_states(("queued", "working")):
        repo.request_cancel(job.id)
    if _worker(request) is not None:
        _worker(request).cancel_all()
    return RedirectResponse(url="/parent/import", status_code=303)


@router.post("/clear")
def clear_finished(viewer: ViewerContext = Depends(require_parent),
                   repo: ImportJobRepository = Depends(get_import_job_repo)):
    repo.clear_finished()
    return RedirectResponse(url="/parent/import", status_code=303)


# -- step 1: pick files --------------------------------------------------------------

@router.get("/browse", response_class=HTMLResponse)
def browse(request: Request, root: int = 0, path: str = "",
           viewer: ViewerContext = Depends(require_parent),
           service: ImportService = Depends(get_import_service),
           repo: ImportJobRepository = Depends(get_import_job_repo)):
    try:
        listing = service.roots.listing(root, path)
    except (ImportPathError, OSError) as e:
        return RedirectResponse(url=f"/parent/import?error={quote(str(e))}", status_code=303)
    imported = repo.imported_paths([str(listing.root / f.rel) for f in listing.files])
    for f in listing.files:
        f.imported = str(listing.root / f.rel) in imported
    return _templates(request).TemplateResponse(request, "parent/import_browse.html", {
        "request": request, "listing": listing, "roots": service.roots.roots(),
        "crumbs": _crumbs(listing.rel), "size_label": size_label,
    })


def _crumbs(rel: str) -> list[tuple[str, str]]:
    out, acc = [], []
    for part in Path(rel).parts if rel else ():
        acc.append(part)
        out.append((part, "/".join(acc)))
    return out


@router.post("/select")
async def select_files(request: Request, viewer: ViewerContext = Depends(require_parent),
                       service: ImportService = Depends(get_import_service)):
    form = await request.form()
    try:
        root = int(str(form.get("root", "0")))
        paths: list[Path] = []
        if form.get("whole_folder"):
            paths = service.roots.videos_under(root, str(form.get("folder", "")))
        else:
            for rel in form.getlist("files"):
                _, target = service.roots.resolve(root, str(rel))
                if target.is_file():
                    paths.append(target)
        batch = service.create_batch(paths)
    except (ImportPathError, ImportFailed, ValueError, OSError) as e:
        return RedirectResponse(url=f"/parent/import?error={quote(str(e))}", status_code=303)
    _prepare_in_background(request, batch)
    return RedirectResponse(url=f"/parent/import/batch/{batch}", status_code=303)


# -- step 2: details ------------------------------------------------------------------

def _job_view(job: ImportJob, service: ImportService) -> dict:
    d = job.details
    probe = d.get("probe") or {}
    plan = probe.get("plan")
    estimate = service.estimate(job)
    if plan == "none":
        plan_line = "Plays as it is"
    elif plan == "remux":
        plan_line = "Repackaged for the player (quick)"
    elif plan == "transcode":
        plan_line = "Converted for the player" + (
            f" (about {_duration(estimate)}, from earlier imports)" if estimate
            else " (time shown once it starts)")
    else:
        plan_line = ""
    if d.get("burn_subtitle_index") is not None:
        plan_line = "Converted, with the chosen subtitles drawn into the picture"
    subtitles = []
    for i, sub in enumerate(d.get("subtitles", [])):
        subtitles.append({
            "n": i, "on": bool(sub.get("on")) or (
                not sub.get("text") and sub.get("index") == d.get("burn_subtitle_index")),
            "label": language_label(sub.get("lang", "")) if sub.get("lang") else "Unknown language",
            "lang": sub.get("lang", ""), "text": sub.get("text", False),
            "where": "next to the file" if sub.get("kind") == "file" else "inside the file",
            "title": sub.get("title", ""),
        })
    audio = [{"index": a["index"],
              "label": " · ".join(x for x in [
                  language_label(a.get("lang", "")) if a.get("lang") else "Track",
                  a.get("title", ""), "commentary" if a.get("commentary") else "",
                  f"{a.get('channels')} ch" if a.get("channels") else ""] if x)}
             for a in d.get("audio", [])]
    return {"job": job, "d": d, "probe": probe, "plan_line": plan_line,
            "subtitles": subtitles, "audio": audio, "size": size_label(job.source_size),
            "length": length_label(probe.get("duration"))}


@router.get("/batch/{batch}", response_class=HTMLResponse)
def batch_page(request: Request, batch: str,
               viewer: ViewerContext = Depends(require_parent),
               service: ImportService = Depends(get_import_service),
               channel_repo: ChannelRepository = Depends(get_channel_repo)):
    jobs = service.batch(batch)
    if not jobs:
        return RedirectResponse(url="/parent/import", status_code=303)
    ready = all(j.details.get("ready") for j in jobs)
    _ensure_default_channel(channel_repo)
    return _templates(request).TemplateResponse(request, "parent/import_batch.html", {
        "request": request, "batch": batch, "ready": ready,
        "read_count": sum(1 for j in jobs if j.details.get("ready")),
        "jobs": [_job_view(j, service) for j in jobs],
        "shows": service.show_groups(jobs) if ready else [],
        "channels": channel_repo.list(), "origins": ORIGIN_LABELS,
        "error": request.query_params.get("error", ""),
        "any_move_blocked": any(not service.can_move(Path(j.source_path)) for j in jobs),
    })


def _job_form(form, job_id: int) -> dict:
    prefix = f"j{job_id}_"
    out: dict = {}
    for key in ("title", "origin", "year", "season", "episode", "link", "description",
                "channel_id", "audio_index"):
        if prefix + key in form:
            out[key] = form.get(prefix + key)
    out["subtitles"] = [str(v) for v in form.getlist(prefix + "subtitles")]
    for key in form.keys():
        if key.startswith(prefix + "sub_lang_"):
            out[key[len(prefix):]] = form.get(key)
    return out


@router.post("/batch/{batch}")
async def save_batch(request: Request, batch: str,
                     viewer: ViewerContext = Depends(require_parent),
                     service: ImportService = Depends(get_import_service)):
    form = await request.form()
    action = str(form.get("action", "save"))
    back = f"/parent/import/batch/{batch}"
    try:
        for job in service.batch(batch):
            if f"j{job.id}_title" in form:
                service.save_job(job.id, _job_form(form, job.id))
        if action == "apply":
            channel = str(form.get("all_channel_id", ""))
            origin = str(form.get("all_origin", ""))
            service.apply_to_batch(batch, channel_id=int(channel) if channel.isdigit() else None,
                                   origin=origin if origin in ORIGINS else None)
        elif action.startswith("show:") or action.startswith("noshow:"):
            accept, _, show = action.partition(":")
            service.group_as_show(batch, show, accept == "show")
        elif action.startswith("lookup:"):
            job_id = action.split(":", 1)[1]
            return RedirectResponse(url=f"/parent/import/jobs/{job_id}/lookup", status_code=303)
        elif action.startswith("remove:"):
            service.remove_from_batch(int(action.split(":", 1)[1]))
        elif action == "import":
            service.queue(batch, str(form.get("mode", "copy")),
                          keep_original=form.get("keep_original") == "1")
            if _worker(request) is not None:
                _worker(request).wake()
            return RedirectResponse(url="/parent/import", status_code=303)
    except (ImportFailed, ImportPathError, ValueError) as e:
        return RedirectResponse(url=f"{back}?error={quote(str(e))}", status_code=303)
    return RedirectResponse(url=back, status_code=303)


@router.post("/batch/{batch}/discard")
def discard_batch(batch: str, viewer: ViewerContext = Depends(require_parent),
                  service: ImportService = Depends(get_import_service)):
    service.discard_batch(batch)
    return RedirectResponse(url="/parent/import", status_code=303)


# -- step 3: picture and description -------------------------------------------------

@router.get("/jobs/{job_id}/lookup", response_class=HTMLResponse)
def lookup_page(request: Request, job_id: int, q: str = "", origin: str = "",
                year: str = "", link: str = "",
                viewer: ViewerContext = Depends(require_parent),
                service: ImportService = Depends(get_import_service),
                repo: ImportJobRepository = Depends(get_import_job_repo),
                lookup: LookupService = Depends(get_lookup_service),
                config: Config = Depends(get_config)):
    job = repo.get(job_id)
    if job is None or job.state != "draft":
        return RedirectResponse(url="/parent/import", status_code=303)
    d = job.details
    origin = origin if origin in ORIGINS else (d.get("origin") or "home")
    query = q or (d.get("show") if d.get("episode") is not None and origin == "movie"
                  else "") or d.get("title") or job.name
    year_value = int(year) if year.isdigit() else d.get("year")
    link = link or d.get("link") or ""
    candidates = []
    searched = bool(q) or origin in ("movie", "youtube") or bool(link)
    if searched and lookup.enabled:
        found = lookup.search(origin, query, year_value, link, config.server.kid_language)
        for n, c in enumerate(found):
            image_name = ""
            if c.image_url:
                try:
                    image_name = service.save_preview_image(job_id, f"cand-{n}",
                                                            lookup.image(c.image_url))
                except (OSError, ValueError) as e:
                    logger.info("lookup image %s skipped: %s", c.image_url, e)
            candidates.append({"n": n, "c": c, "image": image_name})
    try:
        frames = service.frames(job_id)
    except (ImportPathError, ImportFailed):
        frames = []
    return _templates(request).TemplateResponse(request, "parent/import_lookup.html", {
        "request": request, "job": job, "d": d, "origin": origin, "origins": ORIGIN_LABELS,
        "query": query, "year": year_value or "", "link": link,
        "candidates": candidates, "frames": frames, "searched": searched,
        "lookups_enabled": lookup.enabled,
        "sidecars": list(enumerate(d.get("sidecar_pictures") or [])),
        "error": request.query_params.get("error", ""),
    })


@router.post("/jobs/{job_id}/pick")
async def pick(request: Request, job_id: int,
               viewer: ViewerContext = Depends(require_parent),
               service: ImportService = Depends(get_import_service),
               repo: ImportJobRepository = Depends(get_import_job_repo)):
    form = await request.form()
    job = repo.get(job_id)
    if job is None or job.state != "draft":
        return RedirectResponse(url="/parent/import", status_code=303)
    back = f"/parent/import/batch/{job.batch}"
    kind = str(form.get("kind", ""))
    try:
        if kind == "candidate":
            image = service.preview_file(job_id, str(form.get("image", "")))
            year = str(form.get("year", ""))
            service.choose_picture(
                job_id, data=image.read_bytes() if image else None, source="lookup",
                description=str(form.get("description", "")),
                origin_ref=str(form.get("ref", "")),
                original_url=str(form.get("url", "")),
                title=str(form.get("title", "")) if form.get("use_title") else None,
                year=int(year) if year.isdigit() else None)
        elif kind == "frame":
            service.choose_picture(job_id, frame_at=float(str(form.get("at", "1"))),
                                   source="frame")
        elif kind == "sidecar":
            service.choose_picture(job_id, data=service.sidecar_picture(
                job_id, int(str(form.get("n", "0")))), source="sidecar")
    except (ImportFailed, ImportPathError, ValueError, OSError) as e:
        return RedirectResponse(url=f"/parent/import/jobs/{job_id}/lookup?error={quote(str(e))}",
                                status_code=303)
    return RedirectResponse(url=f"{back}#job-{job_id}", status_code=303)


@router.post("/jobs/{job_id}/picture")
async def upload_picture(job_id: int, picture: UploadFile = File(...),
                         viewer: ViewerContext = Depends(require_parent),
                         service: ImportService = Depends(get_import_service),
                         repo: ImportJobRepository = Depends(get_import_job_repo)):
    job = repo.get(job_id)
    if job is None or job.state != "draft":
        return RedirectResponse(url="/parent/import", status_code=303)
    data = await picture.read(MAX_IMAGE_BYTES + 1)
    try:
        service.choose_picture(job_id, data=data, source="upload")
    except (ImportFailed, ValueError) as e:
        return RedirectResponse(url=f"/parent/import/jobs/{job_id}/lookup?error={quote(str(e))}",
                                status_code=303)
    return RedirectResponse(url=f"/parent/import/batch/{job.batch}#job-{job_id}",
                            status_code=303)


@router.post("/jobs/{job_id}/description")
def set_description(job_id: int, description: str = Form(""),
                    viewer: ViewerContext = Depends(require_parent),
                    service: ImportService = Depends(get_import_service),
                    repo: ImportJobRepository = Depends(get_import_job_repo)):
    job = repo.get(job_id)
    if job is None or job.state != "draft":
        return RedirectResponse(url="/parent/import", status_code=303)
    service.choose_picture(job_id, description=description,
                           source=job.details.get("picture_from", ""))
    return RedirectResponse(url=f"/parent/import/batch/{job.batch}#job-{job_id}",
                            status_code=303)


_MIME = {".jpg": "image/jpeg", ".png": "image/png", ".webp": "image/webp", ".gif": "image/gif"}


def _image_response(data: bytes) -> Response:
    mime = _MIME.get(image_extension(data) or "", "application/octet-stream")
    return Response(content=data, media_type=mime, headers={"Cache-Control": "no-store"})


@router.get("/jobs/{job_id}/preview/{name}")
def preview(job_id: int, name: str, viewer: ViewerContext = Depends(require_parent),
            service: ImportService = Depends(get_import_service)):
    path = service.preview_file(job_id, name)
    if path is None:
        return Response(status_code=404)
    return _image_response(path.read_bytes())


@router.get("/jobs/{job_id}/picture")
def current_picture(job_id: int, viewer: ViewerContext = Depends(require_parent),
                    repo: ImportJobRepository = Depends(get_import_job_repo),
                    service: ImportService = Depends(get_import_service)):
    data = repo.picture(job_id)
    if data:
        return _image_response(data)
    job = repo.get(job_id)
    if job is not None and job.details.get("frame_at") is not None:
        frames = {at: name for name, at in service.frames(job_id)}
        name = frames.get(job.details["frame_at"])
        path = service.preview_file(job_id, name) if name else None
        if path is not None:
            return _image_response(path.read_bytes())
    return RedirectResponse(url="/static/placeholder-thumb.svg", status_code=302)
