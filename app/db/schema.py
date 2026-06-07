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


def recover_from_crash(conn: sqlite3.Connection, data_dir: Path) -> None:
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
    videos_dir = data_dir / "videos"
    if videos_dir.exists():
        for vdir in videos_dir.iterdir():
            if not vdir.is_dir():
                continue
            for f in vdir.iterdir():
                if f.suffix == ".part":
                    f.unlink()

    # 3. Verify "ready" videos actually have files on disk.
    #    Downloaded videos live at videos/<id>/video.mp4; uploaded
    #    videos live at uploads/<id>/video.<ext>. If the file is
    #    missing, reset downloaded videos to "pending" for re-download.
    #    Uploaded videos have no re-download path, so mark them as an
    #    "error" instead and leave the parent to delete or re-upload.
    uploads_dir = data_dir / "uploads"
    ready = conn.execute(
        "SELECT video_id, storage_mode FROM videos WHERE download_status = 'ready'"
    ).fetchall()
    missing_downloads = 0
    missing_uploads = 0
    for row in ready:
        vid = row["video_id"]
        mode = row["storage_mode"]
        if mode == "uploaded":
            vdir = uploads_dir / vid
            exists = vdir.exists() and any(
                f.is_file() and f.stem == "video" for f in vdir.iterdir()
            )
            if not exists:
                conn.execute(
                    "UPDATE videos SET download_status = 'error', "
                    "download_error = 'Uploaded file missing after restart' "
                    "WHERE video_id = ?",
                    (vid,),
                )
                missing_uploads += 1
        else:
            if not (videos_dir / vid / "video.mp4").exists():
                conn.execute(
                    "UPDATE videos SET download_status = 'pending', "
                    "cached_at = NULL, file_size = 0, "
                    "download_error = 'File missing after restart' "
                    "WHERE video_id = ?",
                    (vid,),
                )
                missing_downloads += 1
    if missing_downloads or missing_uploads:
        conn.commit()
        if missing_downloads:
            logger.info("Recovery: %d downloaded video(s) missing, reset to pending",
                        missing_downloads)
        if missing_uploads:
            logger.info("Recovery: %d uploaded video(s) missing, marked as error",
                        missing_uploads)
