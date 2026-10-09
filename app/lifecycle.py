from __future__ import annotations
"""Startup work and long-running background tasks.

create_app() calls prepare_data_dir() and startup_jobs() once, and wires
lifespan() into FastAPI. Everything here opens its own SQLite
connection: none of it runs inside a request.
"""

import asyncio
import contextlib
import logging
import secrets
import sqlite3
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import Config
from app.db.connection import create_connection, get_db_path
from app.db.schema import init_schema, recover_from_crash

logger = logging.getLogger("curatables")


def prepare_data_dir(config: Config) -> None:
    """Session secret, schema migrations and crash recovery."""
    if not config.parent.session_secret:
        config.parent.session_secret = secrets.token_hex(32)
        config.save()
    conn = create_connection(get_db_path(config.data_dir))
    try:
        init_schema(conn)
        recover_from_crash(conn, config.data_dir)
    finally:
        conn.close()


def build_content_service(conn: sqlite3.Connection, config: Config, metrics=None):
    """A ContentService outside the request cycle (startup resume)."""
    from app.backends.ytdlp import YtdlpBackend
    from app.repositories import ChannelRepository, SourceRepository, VideoRepository
    from app.services.content import ContentService
    from app.services.media_probe import MediaProbeService
    from app.services.normalize import MediaNormalizer
    from app.services.storage import StorageService
    from app.services.thumbnails import ThumbnailService
    from app.services.video_source import VideoSourceService

    backend = YtdlpBackend(
        impersonate=config.storage.impersonate,
        cookies_from_browser=config.storage.cookies_from_browser,
        cookies_file=config.storage.cookies_file,
    )
    storage = StorageService(
        config.data_dir, backend,
        min_free_bytes=config.storage.min_free_disk_bytes,
        normalizer=MediaNormalizer(MediaProbeService()),
        max_download_bytes=config.storage.max_download_bytes,
    )
    return ContentService(
        VideoRepository(conn), SourceRepository(conn), ChannelRepository(conn),
        VideoSourceService(backend), ThumbnailService(config.data_dir),
        storage, config, metrics=metrics,
    )


def startup_jobs(config: Config, metrics=None) -> None:
    """Re-queue downloads left pending by a restart and sweep abandoned
    upload temp files. Failures are logged, never fatal."""
    try:
        conn = create_connection(get_db_path(config.data_dir))
        try:
            resumed = build_content_service(conn, config, metrics).resume_pending_downloads()
        finally:
            conn.close()
        if resumed:
            logger.info("Resumed %d pending download(s) after restart", resumed)
    except Exception:
        logger.warning("Pending download resume at startup failed (continuing)",
                       exc_info=True)

    try:
        from app.services.storage import StorageService
        storage = StorageService(config.data_dir,
                                 min_free_bytes=config.storage.min_free_disk_bytes)
        swept = storage.sweep_upload_tmp(ttl_hours=24)
        if swept:
            logger.info("Swept %d abandoned upload tmp files", swept)
    except Exception:
        logger.warning("Upload sweep at startup failed (continuing)", exc_info=True)


def _maintenance_pass(config: Config):
    """One sweep: evict cache videos nobody watched for cache_days and
    drop usage events older than events_retention_days."""
    from app.repositories import EventRepository, VideoRepository
    from app.services.storage import StorageService

    conn = create_connection(get_db_path(config.data_dir))
    try:
        report = None
        if config.storage.cache_days > 0:
            storage = StorageService(config.data_dir,
                                     min_free_bytes=config.storage.min_free_disk_bytes)
            report = storage.evict_expired(VideoRepository(conn), config.storage.cache_days)
        pruned = 0
        if config.storage.events_retention_days > 0:
            pruned = EventRepository(conn).delete_older_than(config.storage.events_retention_days)
        return report, pruned
    finally:
        conn.close()


async def maintenance_loop(app: FastAPI, config: Config) -> None:
    """Runs for the life of the app. Reads the config on every tick so
    changes in Settings apply without a restart; the sweep itself runs
    on a worker thread so the event loop stays responsive."""
    logger.info("cache cleanup loop started")
    try:
        while True:
            await asyncio.sleep(max(1, config.storage.cache_cleanup_interval_minutes) * 60)
            if config.storage.cache_cleanup_interval_minutes <= 0:
                continue
            try:
                report, pruned = await asyncio.to_thread(_maintenance_pass, config)
                if report is not None and report.evicted_count:
                    logger.info("cache sweep: evicted %d videos, freed %d bytes",
                                report.evicted_count, report.freed_bytes)
                    app.state.metrics.record_eviction(report.evicted_count)
                if pruned:
                    logger.info("pruned %d old usage events", pruned)
            except Exception:
                logger.exception("cache cleanup sweep failed")
    except asyncio.CancelledError:
        logger.info("cache cleanup loop stopped")
        raise


def make_lifespan(config: Config):
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        from app.services import mdns

        advertiser = None
        if config.server.mdns_enabled:
            advertiser = mdns.ZeroconfAdvertiser(
                name=config.server.mdns_name, port=config.server.port)
            await advertiser.start()
        app.state.mdns = advertiser

        cleanup_task = None
        if config.storage.cache_cleanup_interval_minutes > 0:
            cleanup_task = asyncio.create_task(maintenance_loop(app, config))
        else:
            logger.info("cache cleanup disabled (cache_cleanup_interval_minutes=0)")
        app.state.cache_cleanup_task = cleanup_task

        try:
            yield
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup_task
            if advertiser is not None:
                await advertiser.stop()
    return lifespan
