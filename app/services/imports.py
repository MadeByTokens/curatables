"""Import video files from a drive into the library.

A batch starts as draft rows in import_jobs (one per file). The parent
fills in the details, then queues the batch; a single background worker
runs the jobs one at a time, sharing the download pool's size limit.

Each job works inside ``uploads/.tmp/import-<id>/``. Before the final
commit nothing outside that folder is written, and a cancel, a failure
or a crash only has to delete it. The source file is never modified
before the commit; with "move" it is removed after the row is in.
"""

from __future__ import annotations

import logging
import os
import secrets
import shutil
import sqlite3
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.config import Config
from app.repositories import ChannelRepository, VideoRepository
from app.repositories.import_job_repo import ImportJob, ImportJobRepository
from app.services.ffmpeg_runner import (
    CancelToken, Cancelled, Progress, Stalled, run_ffmpeg, tail_log,
)
from app.services.images import validate_image
from app.services.import_scan import (
    ImportPathError, ImportRoots, ProbeCache, find_sidecars, parse_name,
)
from app.services.ingest import HASH_CHUNK, IngestService, VideoDetails, upload_video_id
from app.services.media_probe import MediaProbeService, ProbeError, ProbeResult, UnsupportedCodec
from app.services.normalize import MediaNormalizer, plan_for
from app.services.storage import StorageService
from app.services.subtitles import language_code, to_vtt
from app.services.thumbnails import ThumbnailService

logger = logging.getLogger(__name__)

ORIGINS = ("movie", "youtube", "web", "home")
MAX_BATCH = 200
PROGRESS_EVERY = 1.0
FRAME_POINTS = (0.1, 0.3, 0.5, 0.7)


class ImportFailed(Exception):
    """A reason the parent can read for a job that did not finish."""


def work_dir_name(job_id: int) -> str:
    return f"import-{job_id}"


def preview_dir_name(job_id: int) -> str:
    return f"import-preview-{job_id}"


def same_filesystem(a: Path, b: Path) -> bool:
    try:
        return a.stat().st_dev == b.stat().st_dev
    except OSError:
        return False


def copy_with_progress(src: Path, dst: Path, on_progress: Callable[[Progress], None],
                       cancel: CancelToken) -> None:
    total = src.stat().st_size
    done = 0
    try:
        with open(src, "rb") as fin, open(dst, "wb") as fout:
            while chunk := fin.read(HASH_CHUNK):
                cancel.check()
                fout.write(chunk)
                done += len(chunk)
                on_progress(Progress(phase="copy", done=done, total=total))
            fout.flush()
            os.fsync(fout.fileno())
    except BaseException:
        dst.unlink(missing_ok=True)
        raise


def episode_key(season: int | None, episode: int | None) -> int | None:
    if episode is None:
        return None
    return (season or 1) * 1000 + episode


def _probe_summary(info: ProbeResult | None, path: Path, faststart: bool,
                   audio_index: int | None) -> dict:
    if info is None:
        return {"ok": False, "error": "This file could not be read as a video."}
    plan = plan_for(info, is_mp4=path.suffix.lower() == ".mp4", faststart=faststart,
                    audio_index=audio_index)
    return {
        "ok": True, "duration": info.duration_seconds, "height": info.height,
        "vcodec": info.codec_name, "acodec": info.audio_codec,
        "plan": plan.action, "kind": plan.kind,
    }


def _default_audio(info: ProbeResult, language: str) -> int | None:
    streams = [a for a in info.audio_streams if not a.commentary] or info.audio_streams
    if len(info.audio_streams) <= 1 or not streams:
        return None
    wanted = language_code(language).split("-")[0]
    for a in streams:
        if wanted and language_code(a.language).split("-")[0] == wanted:
            return a.index
    for a in streams:
        if a.default:
            return a.index
    return streams[0].index


class ImportService:
    """The request side: batches, details, pictures, queueing, cancels."""

    def __init__(self, config: Config, repo: ImportJobRepository, video_repo: VideoRepository,
                 channel_repo: ChannelRepository, ingest: IngestService,
                 probe_cache: ProbeCache):
        self.config = config
        self.repo = repo
        self.video_repo = video_repo
        self.channel_repo = channel_repo
        self.ingest = ingest
        self.roots = ImportRoots(config)
        self.probe_cache = probe_cache

    # -- batches -----------------------------------------------------------

    def create_batch(self, paths: list[Path]) -> str:
        if not paths:
            raise ImportFailed("Pick at least one video.")
        if len(paths) > MAX_BATCH:
            raise ImportFailed(f"Pick at most {MAX_BATCH} videos at a time.")
        batch = secrets.token_hex(8)
        for path in paths:
            st = path.stat()
            guess = parse_name(path)
            self.repo.create_draft(batch, str(path), st.st_size, st.st_mtime, {
                "title": guess.title, "year": guess.year, "season": guess.season,
                "episode": guess.episode, "show": guess.show,
                "origin": "movie" if (guess.year or guess.is_episode) else "home",
                "channel_id": None, "new_channel": "", "description": "",
                "origin_ref": "", "original_url": "", "link": "",
                "audio_index": None, "burn_subtitle_index": None,
                "subtitles": [], "audio": [], "picture_from": "", "frame_at": None,
                "probe": None, "ready": False,
            })
        return batch

    def prepare_job(self, job: ImportJob) -> None:
        """Read the file once: streams, conversion plan, sidecars and a
        default picture. Safe to run on a worker thread."""
        details = dict(job.details)
        try:
            path = self.roots.check_file(job.source_path)
        except ImportPathError as e:
            details.update(probe={"ok": False, "error": str(e)}, ready=True)
            self.repo.update_details(job.id, details)
            return
        info = self.probe_cache.get(path)
        audio_index = _default_audio(info, self.config.server.kid_language) if info else None
        faststart = path.suffix.lower() == ".mp4" and self.probe_cache.probe.is_faststart(path)
        details["probe"] = _probe_summary(info, path, faststart, audio_index)
        if info is not None:
            details["audio"] = [
                {"index": a.index, "lang": a.language, "title": a.title, "codec": a.codec,
                 "channels": a.channels, "commentary": a.commentary}
                for a in info.audio_streams]
            details["audio_index"] = audio_index
            details["subtitles"] = [
                {"kind": "stream", "index": s.index, "lang": language_code(s.language),
                 "title": s.title, "codec": s.codec, "text": s.is_text, "on": s.is_text}
                for s in info.subtitle_streams]
        side = find_sidecars(path)
        details["subtitles"] = [
            {"kind": "file", "path": str(sub.path), "lang": sub.language, "title": sub.path.name,
             "codec": sub.path.suffix.lstrip("."), "text": True, "on": True}
            for sub in side.subtitles] + details["subtitles"]
        details["sidecar_pictures"] = [str(p) for p in side.pictures]
        if side.nfo_title and not details.get("episode"):
            details["title"] = side.nfo_title
        if side.nfo_plot:
            details["description"] = side.nfo_plot[:2000]
        if side.nfo_year:
            details["year"] = side.nfo_year
        if side.pictures and not job.has_picture:
            try:
                data = side.pictures[0].read_bytes()
                validate_image(data)
                self.repo.set_picture(job.id, data)
                details["picture_from"] = "sidecar"
            except (OSError, ValueError):
                pass
        details["ready"] = True
        self.repo.update_details(job.id, details)

    def prepare_batch(self, batch: str) -> None:
        for job in self.repo.list_batch(batch):
            if job.state == "draft" and not job.details.get("ready"):
                try:
                    self.prepare_job(job)
                except Exception:
                    logger.exception("import: preparing %s failed", job.source_path)
                    details = dict(job.details, ready=True,
                                   probe={"ok": False, "error": "This file could not be read."})
                    self.repo.update_details(job.id, details)

    def batch(self, batch: str) -> list[ImportJob]:
        return [j for j in self.repo.list_batch(batch) if j.state == "draft"]

    def show_groups(self, jobs: list[ImportJob]) -> list[dict]:
        """Episodes of the same show, as suggestions for one channel each."""
        groups: dict[str, list[ImportJob]] = {}
        for job in jobs:
            show = (job.details.get("show") or "").strip()
            if job.details.get("episode") is not None and show:
                groups.setdefault(show, []).append(job)
        out = []
        for show, members in sorted(groups.items()):
            if len(members) < 2:
                continue
            out.append({"show": show, "count": len(members),
                        "job_ids": [j.id for j in members],
                        "accepted": all(j.details.get("new_channel") == show for j in members)})
        return out

    def save_job(self, job_id: int, form: dict) -> ImportJob:
        """Apply the fields the details form sent; fields it did not send
        keep their saved value."""
        job = self._draft(job_id)
        details = dict(job.details)
        if "title" in form:
            title = " ".join(str(form["title"]).split())[:200]
            if title:
                details["title"] = title
        if "origin" in form:
            origin = str(form["origin"])
            details["origin"] = origin if origin in ORIGINS else "home"
        for key in ("year", "season", "episode"):
            if key in form:
                raw = str(form[key]).strip()
                details[key] = int(raw) if raw.isdigit() else None
        if "link" in form:
            details["link"] = str(form["link"]).strip()[:500]
        if "description" in form:
            details["description"] = str(form["description"])[:2000]
        if "channel_id" in form:
            channel = str(form["channel_id"]).strip()
            details["channel_id"] = int(channel) if channel.isdigit() else None
        if "audio_index" in form:
            audio = str(form["audio_index"]).strip()
            known_audio = {a["index"] for a in details.get("audio", [])}
            details["audio_index"] = int(audio) \
                if audio.isdigit() and int(audio) in known_audio else None
        chosen = set(form.get("subtitles", []))
        burn = None
        for i, sub in enumerate(details.get("subtitles", [])):
            key = str(i)
            sub["on"] = key in chosen and sub.get("text", False)
            if key in chosen and not sub.get("text") and burn is None:
                burn = sub["index"]
            if f"sub_lang_{i}" in form:
                sub["lang"] = language_code(str(form[f"sub_lang_{i}"]))
        details["burn_subtitle_index"] = burn
        self.repo.update_details(job_id, details)
        return self._draft(job_id)

    def apply_to_batch(self, batch: str, *, channel_id: int | None = None,
                       origin: str | None = None) -> None:
        for job in self.batch(batch):
            details = dict(job.details)
            if channel_id is not None and not details.get("new_channel"):
                details["channel_id"] = channel_id
            if origin in ORIGINS:
                details["origin"] = origin
            self.repo.update_details(job.id, details)

    def group_as_show(self, batch: str, show: str, accept: bool) -> None:
        for job in self.batch(batch):
            if (job.details.get("show") or "").strip() == show \
                    and job.details.get("episode") is not None:
                details = dict(job.details, new_channel=show if accept else "")
                if accept:
                    details["channel_id"] = None
                    details["origin"] = "movie"
                self.repo.update_details(job.id, details)

    def remove_from_batch(self, job_id: int) -> None:
        job = self._draft(job_id)
        self.repo.finish(job.id, "cancelled")
        self._drop_preview(job.id)

    def discard_batch(self, batch: str) -> None:
        for job in self.batch(batch):
            self._drop_preview(job.id)
        self.repo.delete_drafts(batch)

    # -- pictures ------------------------------------------------------------

    def preview_dir(self, job_id: int) -> Path:
        return self.ingest.work_dir(preview_dir_name(job_id))

    def _drop_preview(self, job_id: int) -> None:
        shutil.rmtree(self.ingest.storage.upload_tmp_dir() / preview_dir_name(job_id),
                      ignore_errors=True)

    def frames(self, job_id: int) -> list[tuple[str, float]]:
        """Four frames from the video to choose from: (file name, seconds)."""
        job = self._draft(job_id)
        path = self.roots.check_file(job.source_path)
        duration = float((job.details.get("probe") or {}).get("duration") or 0)
        out = []
        folder = self.preview_dir(job_id)
        for n, point in enumerate(FRAME_POINTS):
            at = round(duration * point, 1) if duration else float(n * 5 + 1)
            name = f"frame-{n}.jpg"
            target = folder / name
            if not target.exists():
                try:
                    subprocess.run(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-ss", str(at),
                         "-i", str(path), "-frames:v", "1", "-vf", "scale=480:-2",
                         "-q:v", "3", str(target)],
                        capture_output=True, timeout=60, stdin=subprocess.DEVNULL)
                except (OSError, subprocess.TimeoutExpired):
                    continue
            if target.exists():
                out.append((name, at))
        return out

    def preview_file(self, job_id: int, name: str) -> Path | None:
        safe = Path(name).name
        path = self.ingest.storage.upload_tmp_dir() / preview_dir_name(job_id) / safe
        return path if path.is_file() else None

    def save_preview_image(self, job_id: int, name: str, data: bytes) -> str:
        validate_image(data)
        safe = Path(name).name
        (self.preview_dir(job_id) / safe).write_bytes(data)
        return safe

    def choose_picture(self, job_id: int, *, data: bytes | None = None,
                       frame_at: float | None = None, source: str = "",
                       description: str | None = None, origin_ref: str | None = None,
                       original_url: str | None = None, title: str | None = None,
                       year: int | None = None) -> None:
        job = self._draft(job_id)
        details = dict(job.details)
        if data is not None:
            validate_image(data)
            self.repo.set_picture(job_id, data)
            details["frame_at"] = None
        elif frame_at is not None:
            self.repo.set_picture(job_id, None)
            details["frame_at"] = frame_at
        details["picture_from"] = source
        if description is not None:
            details["description"] = description[:2000]
        if origin_ref is not None:
            details["origin_ref"] = origin_ref[:100]
        if original_url is not None:
            details["original_url"] = original_url[:500]
        if title:
            details["title"] = title[:200]
        if year:
            details["year"] = year
        self.repo.update_details(job_id, details)

    def sidecar_picture(self, job_id: int, n: int) -> bytes:
        job = self._draft(job_id)
        pictures = job.details.get("sidecar_pictures") or []
        if not 0 <= n < len(pictures):
            raise ImportFailed("That picture is not there any more.")
        path = Path(pictures[n])
        self.roots.check_file(job.source_path)
        if path.parent != Path(job.source_path).parent:
            raise ImportFailed("That picture is not next to the video.")
        data = path.read_bytes()
        validate_image(data)
        return data

    # -- queue ----------------------------------------------------------------

    def can_move(self, path: Path) -> bool:
        return os.access(path.parent, os.W_OK) and os.access(path, os.R_OK)

    def queue(self, batch: str, mode: str, keep_original: bool) -> int:
        jobs = self.batch(batch)
        if not jobs:
            raise ImportFailed("There is nothing left in this batch to import.")
        unready = [j for j in jobs if not j.details.get("ready")]
        if unready:
            raise ImportFailed("Some files are still being read. Wait a moment and try again.")
        bad = [j for j in jobs if not (j.details.get("probe") or {}).get("ok")]
        if bad:
            raise ImportFailed(f'"{bad[0].name}" cannot be imported: '
                               f'{(bad[0].details.get("probe") or {}).get("error", "")} '
                               "Remove it from the batch first.")
        missing = [j for j in jobs if not j.details.get("new_channel")
                   and j.details.get("channel_id") is None]
        if missing:
            raise ImportFailed(f'Choose a channel for "{missing[0].title}".')
        mode = mode if mode in ("move", "copy") else "copy"
        channels: dict[str, int] = {}
        for job in jobs:
            show = job.details.get("new_channel") or ""
            if show:
                if show not in channels:
                    channels[show] = self.channel_repo.create(show)
                self.repo.update_details(job.id, dict(job.details, channel_id=channels[show]))
            self._drop_preview(job.id)
        count = self.repo.queue_batch(batch, mode, keep_original)
        if mode == "move":
            for job in jobs:
                if not self.can_move(Path(job.source_path)):
                    self.repo.set_mode(job.id, "copy")
        return count

    def estimate(self, job: ImportJob) -> float | None:
        """Seconds the conversion is expected to take, from past jobs."""
        probe = job.details.get("probe") or {}
        if probe.get("plan") == "none":
            return None
        speed = self.repo.measured_speed(probe.get("kind", ""))
        duration = float(probe.get("duration") or 0)
        return duration / speed if speed and duration else None

    def _draft(self, job_id: int) -> ImportJob:
        job = self.repo.get(job_id)
        if job is None or job.state != "draft":
            raise ImportFailed("That import is no longer being set up.")
        return job


@dataclass
class _Reporter:
    repo: ImportJobRepository
    job_id: int
    cancel: CancelToken
    last: float = 0.0
    last_check: float = 0.0

    def __call__(self, progress: Progress) -> None:
        now = time.monotonic()
        if now - self.last_check >= 2.0:
            self.last_check = now
            job = self.repo.get(self.job_id)
            if job is not None and job.state == "cancelling":
                self.cancel.cancel()
        finished = progress.total and progress.done >= progress.total
        if now - self.last < PROGRESS_EVERY and not finished:
            return
        self.last = now
        self.repo.progress(self.job_id, progress.phase, progress.done, progress.total,
                           progress.speed, progress.eta_seconds, progress.stalled_for)


class ImportRunner:
    """Runs one queued job to an outcome. Synchronous."""

    def __init__(self, config: Config, conn: sqlite3.Connection, ingest: IngestService,
                 normalizer: MediaNormalizer):
        self.config = config
        self.repo = ImportJobRepository(conn)
        self.video_repo = VideoRepository(conn)
        self.ingest = ingest
        self.normalizer = normalizer
        self.roots = ImportRoots(config)

    def run(self, job_id: int, cancel: CancelToken) -> None:
        if not self.repo.claim(job_id):
            return
        job = self.repo.get(job_id)
        assert job is not None
        work = self.ingest.work_dir(work_dir_name(job_id))
        report = _Reporter(self.repo, job_id, cancel)
        try:
            outcome = self._run(job, work, report, cancel)
            self.repo.finish(job_id, "done", **outcome)
        except Cancelled:
            shutil.rmtree(work, ignore_errors=True)
            self.repo.finish(job_id, "cancelled")
        except (ImportFailed, ImportPathError, ProbeError) as e:
            shutil.rmtree(work, ignore_errors=True)
            self.repo.finish(job_id, "failed", error=str(e))
        except UnsupportedCodec as e:
            shutil.rmtree(work, ignore_errors=True)
            self.repo.finish(job_id, "failed", error=e.conversion_hint)
        except Exception as e:
            logger.exception("import job %s crashed", job_id)
            shutil.rmtree(work, ignore_errors=True)
            self.repo.finish(job_id, "failed", error=f"Something went wrong: {e}")

    def _run(self, job: ImportJob, work: Path, report: _Reporter, cancel: CancelToken) -> dict:
        details = job.details
        src = self.roots.check_file(job.source_path)
        st = src.stat()
        if st.st_size != job.source_size or abs(st.st_mtime - job.source_mtime) > 1:
            raise ImportFailed("The file changed after it was picked. Pick it again.")
        allowed, reason = self.ingest.storage.check_can_write(st.st_size)
        if not allowed:
            raise ImportFailed(reason)

        info = self.ingest.probe.validate(src, original_filename=src.name)
        digest = self.ingest.hash_file(src, on_progress=report, cancel=cancel)
        video_id = upload_video_id(digest)
        if self.video_repo.get(video_id) is not None:
            shutil.rmtree(work, ignore_errors=True)
            return {"video_id": video_id, "error": "Already in the library; nothing was copied."}

        converted = self.normalizer.convert(
            src, work / "video.mp4", on_progress=report, cancel=cancel,
            audio_index=details.get("audio_index"),
            burn_subtitle_index=details.get("burn_subtitle_index"))
        if converted.action == "skipped":
            raise ImportFailed(f"The video could not be converted ({converted.reason}).")
        renamed_source = False
        if converted.action == "none":
            if job.mode == "move" and same_filesystem(src, work):
                final = src
                renamed_source = True
            else:
                final = work / f"video{src.suffix.lower()}"
                copy_with_progress(src, final, report, cancel)
        else:
            final = converted.path
            try:
                info = self.ingest.probe.probe(final)
            except ProbeError:
                pass

        self._subtitles(src, details, work, info, report, cancel)
        cancel.check()

        report(Progress(phase="finish", done=0, total=1))
        video = self.ingest.commit(work, final, video_id, info, VideoDetails(
            title=details.get("title") or src.stem,
            channel_id=details.get("channel_id"),
            description=details.get("description") or "",
            original_url=details.get("original_url") or details.get("link") or "",
            upload_date=f"{details['year']}-01-01" if details.get("year") else "",
            thumbnail_bytes=self.repo.picture(job.id),
            extra={"origin": details.get("origin") or "",
                   "origin_ref": details.get("origin_ref") or "",
                   "source_path": str(src),
                   "episode_key": episode_key(details.get("season"), details.get("episode"))
                   if details.get("new_channel") or details.get("episode") is not None
                   else None}))
        frame_at = details.get("frame_at")
        if frame_at is not None and not job.has_picture:
            stamp = time.strftime("%H:%M:%S", time.gmtime(float(frame_at)))
            self.ingest.thumbnails.extract_frame(
                video.video_id, timestamp=stamp,
                source_path=self.ingest.storage.video_file_path(video))
        if job.mode == "move" and not renamed_source:
            if converted.action == "none" or not job.keep_original:
                try:
                    src.unlink()
                except OSError as e:
                    logger.warning("import: could not remove %s after import: %s", src, e)
        return {"video_id": video_id, "kind": converted.kind,
                "media_seconds": converted.media_seconds,
                "wall_seconds": converted.wall_seconds}

    def _subtitles(self, src: Path, details: dict, work: Path, info: ProbeResult,
                   report: _Reporter, cancel: CancelToken) -> None:
        used: set[str] = set()

        def target(lang: str) -> Path:
            base = lang or "und"
            name, n = base, 2
            while name in used:
                name, n = f"{base}{n}", n + 1
            used.add(name)
            return work / f"video.{name}.vtt"

        for sub in details.get("subtitles", []):
            if not sub.get("on"):
                continue
            cancel.check()
            if sub.get("kind") == "file":
                path = Path(sub["path"])
                if path.parent != src.parent or not path.is_file():
                    continue
                try:
                    data = path.read_bytes()
                    target(sub.get("lang", "")).write_text(to_vtt(data, path.suffix),
                                                           encoding="utf-8")
                except OSError as e:
                    logger.warning("import: subtitle %s skipped: %s", path, e)
            elif sub.get("kind") == "stream" and sub.get("text"):
                out = target(sub.get("lang", ""))
                log = work / "subtitles.log"
                try:
                    code = run_ffmpeg(
                        ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
                         "-map", f"0:{int(sub['index'])}", "-c:s", "webvtt", str(out)],
                        duration=info.duration_seconds, log_path=log,
                        on_progress=lambda p: report(Progress(
                            phase="subtitles", done=p.done, total=p.total, speed=p.speed,
                            eta_seconds=p.eta_seconds, stalled_for=p.stalled_for)),
                        cancel=cancel, phase="subtitles")
                except Stalled:
                    code = -1
                if code != 0:
                    logger.warning("import: subtitle stream %s skipped: %s", sub["index"],
                                   tail_log(log))
                    out.unlink(missing_ok=True)
                log.unlink(missing_ok=True)


class ImportWorker:
    """One background thread that runs queued jobs, one at a time."""

    def __init__(self, config: Config, connect: Callable[[], sqlite3.Connection],
                 runner_factory: Callable[[sqlite3.Connection], ImportRunner],
                 submit: Callable[[str, Callable[[], None]], bool] | None = None):
        self.config = config
        self.connect = connect
        self.runner_factory = runner_factory
        self.submit = submit
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._tokens: dict[int, CancelToken] = {}
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        conn = self.connect()
        try:
            repo = ImportJobRepository(conn)
            repo.recover_after_restart()
            self.sweep(conn)
        finally:
            conn.close()
        self._thread = threading.Thread(target=self._loop, name="imports", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            for token in self._tokens.values():
                token.cancel()
        if self._thread is not None:
            self._thread.join(timeout=15)

    def wake(self) -> None:
        self._wake.set()

    def cancel(self, job_id: int) -> None:
        with self._lock:
            token = self._tokens.get(job_id)
        if token is not None:
            token.cancel()

    def cancel_all(self) -> None:
        with self._lock:
            tokens = list(self._tokens.values())
        for token in tokens:
            token.cancel()

    def sweep(self, conn: sqlite3.Connection) -> int:
        """Delete job and preview folders no live job owns."""
        repo = ImportJobRepository(conn)
        tmp = self.config.media_dir / "uploads" / ".tmp"
        if not tmp.is_dir():
            return 0
        keep_work = {work_dir_name(j.id) for j in repo.list_states(("working",))}
        keep_preview = {preview_dir_name(j.id) for j in repo.list_states(("draft",))}
        removed = 0
        for entry in tmp.iterdir():
            if not entry.is_dir() or not entry.name.startswith("import-"):
                continue
            if entry.name in keep_work or entry.name in keep_preview:
                continue
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
        for job_id in repo.delete_stale_drafts():
            shutil.rmtree(tmp / preview_dir_name(job_id), ignore_errors=True)
        return removed

    def _loop(self) -> None:
        while not self._stop.is_set():
            ran = False
            try:
                ran = self._run_next()
            except Exception:
                logger.exception("import worker pass failed")
            if not ran:
                self._wake.wait(timeout=5)
                self._wake.clear()

    def _run_next(self) -> bool:
        conn = self.connect()
        try:
            job = ImportJobRepository(conn).next_queued()
        finally:
            conn.close()
        if job is None:
            return False
        token = CancelToken()
        with self._lock:
            self._tokens[job.id] = token
        done = threading.Event()

        def run() -> None:
            c = self.connect()
            try:
                self.runner_factory(c).run(job.id, token)
            finally:
                c.close()
                done.set()

        try:
            if self.submit is None or not self.submit(f"import-{job.id}", run):
                run()
            while not done.wait(timeout=1):
                if self._stop.is_set():
                    token.cancel()
        finally:
            with self._lock:
                self._tokens.pop(job.id, None)
        return True


def build_worker(config: Config, connect: Callable[[], sqlite3.Connection],
                 submit: Callable[[str, Callable[[], None]], bool] | None = None
                 ) -> ImportWorker:
    probe = MediaProbeService()
    normalizer = MediaNormalizer(probe)

    def runner(conn: sqlite3.Connection) -> ImportRunner:
        storage = StorageService(config.media_dir,
                                 min_free_bytes=config.storage.min_free_disk_bytes,
                                 normalizer=normalizer)
        ingest = IngestService(storage, probe, VideoRepository(conn),
                               ThumbnailService(config.media_dir))
        return ImportRunner(config, conn, ingest, normalizer)

    return ImportWorker(config, connect, runner, submit)
