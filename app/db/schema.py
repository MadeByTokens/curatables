"""Schema initialization and crash recovery.

The schema is now managed by the forward-only migrator in
`app/db/migrator.py`. Each change is a numbered file under
`app/db/migrations/`. `init_schema` is a thin compatibility shim
that calls the migrator — existing callers (tests, main app)
don't need to change.

`schema.sql` remains as a snapshot / documentation of the current
schema but is no longer executed at runtime.
"""

import logging
import sqlite3
from pathlib import Path

logger = logging.getLogger(__name__)


def init_schema(conn: sqlite3.Connection) -> None:
    """Run any outstanding migrations. Safe to call on every startup —
    the migrator skips migrations already recorded in schema_migrations."""
    from app.db.migrator import apply_pending, migrations_dir
    count = apply_pending(conn, migrations_dir())
    if count:
        logger.info("Applied %d migration(s).", count)


def recover_from_crash(conn: sqlite3.Connection, media_dir: Path) -> None:
    """Fix state left inconsistent by an unclean shutdown.

    Called on every startup. Must be idempotent and safe to run when
    there was no crash.
    """
    # 1. Reset stuck downloads — any video in "downloading" state was
    #    interrupted. Reset to "pending" so they retry.
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

    # 2. Clean up partial video files (.part files left by yt-dlp)
    videos_dir = media_dir / "videos"
    if videos_dir.exists():
        for vdir in videos_dir.iterdir():
            if not vdir.is_dir():
                continue
            for f in vdir.iterdir():
                if f.suffix == ".part":
                    f.unlink()

    # 3. Verify "ready" videos actually have files on disk.
    #    Downloaded videos live at videos/<id>/video.<ext>; uploaded
    #    videos at uploads/<id>/video.<ext>. A missing download goes back
    #    to "pending" for re-download; a missing upload has no source, so
    #    it is marked "error" for the parent to deal with.
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


MASS_MISSING_MIN = 5
_VIDEO_SUFFIXES = (".mp4", ".webm", ".mkv", ".mov", ".m4v", ".avi", ".mpg", ".mpeg")


def _has_video_file(vdir: Path) -> bool:
    return vdir.is_dir() and any(
        f.is_file() and f.stem == "video" and f.suffix.lower() in _VIDEO_SUFFIXES
        for f in vdir.iterdir())
