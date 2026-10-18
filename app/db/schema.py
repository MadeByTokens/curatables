"""Schema initialization and crash recovery."""

import logging
import sqlite3
from pathlib import Path

from app.db.migrator import apply_pending, migrations_dir

logger = logging.getLogger(__name__)

# At least this many missing files, and at least half of all ready
# videos, looks like an unmounted drive rather than lost files.
MASS_MISSING_MIN = 5
_VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".mpg", ".mpeg")


def init_schema(conn: sqlite3.Connection) -> None:
    """Apply any outstanding migrations; safe to call on every startup."""
    count = apply_pending(conn, migrations_dir())
    if count:
        logger.info("Applied %d migration(s).", count)


def recover_from_crash(conn: sqlite3.Connection, media_dir: Path) -> None:
    """Fix state left inconsistent by an unclean shutdown.

    Called on every startup. Must be idempotent and safe to run when
    there was no crash.
    """
    # A video still marked "downloading" was interrupted; queue it again.
    stuck = conn.execute(
        "SELECT video_id FROM videos WHERE download_status = 'downloading'"
    ).fetchall()
    if stuck:
        conn.execute(
            "UPDATE videos SET download_status = 'pending', "
            "download_error = 'Interrupted by server restart' "
            "WHERE download_status = 'downloading'"
        )
        conn.commit()
        logger.info("Recovery: reset %d interrupted download(s) to pending", len(stuck))

    if not media_dir.is_dir():
        logger.warning("Recovery: the media folder %s is missing (drive unplugged?); "
                       "video files were not checked.", media_dir)
        return

    # Partial files left behind by yt-dlp, and a conversion the power cut
    # stopped (video.norm.mp4 and its log); nothing is downloading or
    # converting yet, so none of these is in use. A download the parent
    # paused keeps its partial file, to carry on from there.
    paused = {r["video_id"] for r in conn.execute(
        "SELECT video_id FROM videos WHERE download_status = 'paused'").fetchall()}
    videos_dir = media_dir / "videos"
    if videos_dir.exists():
        for vdir in videos_dir.iterdir():
            if not vdir.is_dir() or vdir.name in paused:
                continue
            for f in vdir.iterdir():
                if (f.suffix in (".part", ".ytdl") or ".part-Frag" in f.name
                        or f.name.startswith("video.norm.")):
                    f.unlink(missing_ok=True)

    # A "ready" video whose file is gone: a download (videos/<id>/) goes
    # back to "pending" to be fetched again; an upload (uploads/<id>/)
    # has no source, so it becomes "error" for the parent to deal with.
    uploads_dir = media_dir / "uploads"
    ready = conn.execute(
        "SELECT video_id, storage_mode FROM videos WHERE download_status = 'ready'"
    ).fetchall()
    missing_downloads: list[str] = []
    missing_uploads: list[str] = []
    for row in ready:
        vid = row["video_id"]
        base = uploads_dir if row["storage_mode"] == "uploaded" else videos_dir
        if not _has_video_file(base / vid):
            (missing_uploads if row["storage_mode"] == "uploaded"
             else missing_downloads).append(vid)

    missing = len(missing_downloads) + len(missing_uploads)
    if missing >= MASS_MISSING_MIN and missing * 2 >= len(ready):
        logger.error(
            "Recovery: %d of %d ready videos have no file in %s. That looks "
            "like a missing or unmounted drive, so nothing was reset. "
            "Mount the drive and restart curatables.",
            missing, len(ready), media_dir)
        return

    for vid in missing_downloads:
        conn.execute(
            "UPDATE videos SET download_status = 'pending', "
            "cached_at = NULL, file_size = 0, "
            "download_error = 'File missing after restart' "
            "WHERE video_id = ?",
            (vid,),
        )
    for vid in missing_uploads:
        conn.execute(
            "UPDATE videos SET download_status = 'error', "
            "download_error = 'Uploaded file missing after restart' "
            "WHERE video_id = ?",
            (vid,),
        )
    if missing:
        conn.commit()
        if missing_downloads:
            logger.info("Recovery: %d downloaded video(s) missing, reset to pending",
                        len(missing_downloads))
        if missing_uploads:
            logger.info("Recovery: %d uploaded video(s) missing, marked as error",
                        len(missing_uploads))


def _has_video_file(vdir: Path) -> bool:
    return vdir.is_dir() and any(
        f.is_file() and f.stem == "video" and f.suffix.lower() in _VIDEO_SUFFIXES
        for f in vdir.iterdir())
