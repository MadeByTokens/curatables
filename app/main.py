"""FastAPI application factory."""

import logging
import os
import secrets
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import RedirectResponse

from app.config import load_config, ensure_directories
from app.db.connection import create_connection, get_db_path
from app.db.schema import init_schema, recover_from_crash
from app.dependencies import NotAuthenticated, NotAChild
from app.logging_config import setup_logging
from app.middleware import (
    RequestLoggingMiddleware, BodySizeLimitMiddleware, RequestIDMiddleware,
    CSRFMiddleware, PrometheusMiddleware,
)
from app.services.csrf import CSRFService
from app.services.metrics import MetricsService


def create_app(data_dir: str | None = None, log_level: str | None = None) -> FastAPI:
    config = load_config(data_dir)
    ensure_directories(config)

    # Initialize logging — CLI log_level overrides config
    effective_log_level = log_level or config.server.log_level
    log_dir = config.data_dir / "logs"
    setup_logging(effective_log_level, log_dir)

    if not config.parent.session_secret:
        config.parent.session_secret = secrets.token_hex(32)
        config.save()

    # Run schema init and crash recovery with a temporary connection
    db_path = get_db_path(config.data_dir)
    conn = create_connection(db_path)
    init_schema(conn)
    recover_from_crash(conn, config.data_dir)
    conn.close()

    # Resume any videos left in 'pending' after the crash-recovery pass.
    # recover_from_crash moves interrupted 'downloading' rows back to
    # 'pending', but nothing re-kicks them unless we do so here. Each
    # download runs in its own daemon thread with its own connection,
    # so this call returns immediately after queuing.
    try:
        from app.backends.ytdlp import YtdlpBackend
        from app.services.video_source import VideoSourceService
        from app.services.thumbnails import ThumbnailService
        from app.services.storage import StorageService
        from app.services.normalize import MediaNormalizer
        from app.services.media_probe import MediaProbeService
        from app.services.content import ContentService
        from app.repositories import (
            VideoRepository, SourceRepository, ChannelRepository,
        )

        _resume_conn = create_connection(db_path)
        _resume_backend = YtdlpBackend(
            impersonate=config.storage.impersonate,
            cookies_from_browser=config.storage.cookies_from_browser,
            cookies_file=config.storage.cookies_file,
        )
        _resume_content = ContentService(
            VideoRepository(_resume_conn),
            SourceRepository(_resume_conn),
            ChannelRepository(_resume_conn),
            VideoSourceService(_resume_backend),
            ThumbnailService(config.data_dir),
            StorageService(config.data_dir, _resume_backend,
                           min_free_bytes=config.storage.min_free_disk_bytes,
                           normalizer=MediaNormalizer(MediaProbeService())),
            config,
        )
        _resumed = _resume_content.resume_pending_downloads()
        _resume_conn.close()
        if _resumed:
            import logging
            logging.getLogger("curatables").info(
                "Resumed %d pending download(s) after restart", _resumed)
    except Exception:
        import logging
        logging.getLogger("curatables").warning(
            "Pending download resume at startup failed (continuing)",
            exc_info=True)

    # Sweep abandoned upload tmp files from previous runs.
    from app.services.storage import StorageService
    from app.services.uploads import UploadService
    try:
        _sweep_storage = StorageService(config.data_dir, backend=None,
                                        min_free_bytes=config.storage.min_free_disk_bytes)
        _sweep_uploads = UploadService(_sweep_storage, probe=None, video_repo=None,
                                       channel_repo=None, thumbnails=None)
        _swept = _sweep_uploads.sweep_abandoned(ttl_hours=24)
        if _swept:
            import logging
            logging.getLogger("curatables").info(
                "Swept %d abandoned upload tmp files", _swept)
    except Exception:
        import logging
        logging.getLogger("curatables").warning(
            "Upload sweep at startup failed (continuing)")

    # mDNS advertiser lives for the lifetime of the app. Stored on
    # app.state so tests can observe/replace it without reaching into
    # the lifespan closure. start()/stop() are async because they drive
    # zeroconf.asyncio.AsyncZeroconf — the sync Zeroconf class refuses
    # to run from inside an active event loop (EventLoopBlocked).
    from app.services.mdns import ZeroconfAdvertiser

    async def _cache_cleanup_loop():
        """Periodic cache eviction sweep. Runs for the life of the app.

        Reads the current config on every tick so changing
        cache_days / interval in /parent/settings takes effect without
        a restart. Each iteration runs the synchronous sweep on a
        thread (SQLite + filesystem) so the event loop stays responsive.
        Cancellation in the lifespan finally-block is the clean
        shutdown signal.
        """
        import asyncio
        import contextlib
        from app.backends.ytdlp import YtdlpBackend
        from app.db.connection import create_connection, get_db_path
        from app.repositories import VideoRepository
        from app.services.storage import StorageService

        log = logging.getLogger("curatables")
        log.info("cache cleanup loop started")
        try:
            while True:
                interval_min = max(1, config.storage.cache_cleanup_interval_minutes)
                await asyncio.sleep(interval_min * 60)
                if (config.storage.cache_cleanup_interval_minutes <= 0
                        or config.storage.cache_days <= 0):
                    continue  # disabled at runtime, keep the task alive in case it's re-enabled
                try:
                    def _run_sweep():
                        conn = create_connection(get_db_path(config.data_dir))
                        try:
                            # No backend needed for eviction — we only
                            # delete files and update DB rows.
                            storage = StorageService(
                                config.data_dir, backend=None,
                                min_free_bytes=config.storage.min_free_disk_bytes)
                            return storage.evict_expired(
                                VideoRepository(conn),
                                config.storage.cache_days)
                        finally:
                            conn.close()

                    report = await asyncio.to_thread(_run_sweep)
                    if report.evicted_count:
                        log.info("cache sweep: evicted %d videos, freed %d bytes",
                                 report.evicted_count, report.freed_bytes)
                        app.state.metrics.record_eviction(report.evicted_count)
                except Exception:
                    log.exception("cache cleanup sweep failed")
        except asyncio.CancelledError:
            log.info("cache cleanup loop stopped")
            raise

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        import asyncio
        import contextlib
        import logging as _logging
        advertiser = None
        if config.server.mdns_enabled:
            advertiser = ZeroconfAdvertiser(
                name=config.server.mdns_name,
                port=config.server.port,
            )
            await advertiser.start()
            app.state.mdns = advertiser
        else:
            app.state.mdns = None

        cleanup_task = None
        if config.storage.cache_cleanup_interval_minutes > 0:
            cleanup_task = asyncio.create_task(_cache_cleanup_loop())
            app.state.cache_cleanup_task = cleanup_task
        else:
            app.state.cache_cleanup_task = None
            _logging.getLogger("curatables").info(
                "cache cleanup disabled (cache_cleanup_interval_minutes=0)")

        try:
            yield
        finally:
            if cleanup_task is not None:
                cleanup_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await cleanup_task
            if advertiser is not None:
                await advertiser.stop()

    app = FastAPI(title="curatables", docs_url=None, redoc_url=None, lifespan=lifespan)

    app.add_middleware(RequestLoggingMiddleware)
    # RequestIDMiddleware stamps each request with a correlation ID in a
    # contextvar. The logging filter injects it into every log record so
    # a grep of the logs can follow a single request across modules.
    # Added OUTSIDE RequestLoggingMiddleware (via later add_middleware
    # ordering) so the logging middleware itself sees the set ID.
    app.add_middleware(RequestIDMiddleware)
    # BodySizeLimitMiddleware caps request body size early so a malicious
    # POST to /watch/<id>/comment with a 500MB body can't OOM the server.
    # Legitimate upload endpoints (video/thumbnail uploads) get the larger
    # max_upload_bytes ceiling.
    app.add_middleware(
        BodySizeLimitMiddleware,
        upload_limit=config.storage.max_upload_bytes,
    )
    # SessionMiddleware is added below, AFTER the @app.middleware("http")
    # decorators. Starlette builds the middleware stack by inserting each
    # new add_middleware at index 0 and then wrapping outside-in in
    # reversed order, so the LAST add_middleware call becomes the
    # outermost wrapper and runs first on an incoming request. We need
    # SessionMiddleware to run before the @middleware decorators so that
    # request.session is populated when the anonymous_kid_redirect
    # middleware reads it.

    app.state.config = config

    # Process start time used by /healthz to report uptime. monotonic()
    # is immune to wall-clock jumps, which matters for an appliance that
    # stays up for months at a time.
    import time as _time
    app.state.start_time = _time.monotonic()

    # Prometheus metrics service. Always instantiated; the recorders
    # no-op when prometheus_enabled=False so call sites don't branch.
    # /metrics returns 404 when disabled.
    app.state.metrics = MetricsService(enabled=config.server.prometheus_enabled)

    # CSRF service — shared between CSRFMiddleware (validates) and the
    # Jinja csrf_token context processor (mints). Created here, ahead
    # of the Jinja2Templates constructor that closes over it.
    csrf_service = CSRFService(secret_key=config.parent.session_secret)
    app.state.csrf = csrf_service

    # Templates — base directory, themes can overlay later
    templates_dir = os.path.join(os.path.dirname(__file__), "templates", "base")

    def _csrf_context(request):
        """Injects a fresh csrf_token into every TemplateResponse so
        forms can render <input name="csrf_token" value="{{ csrf_token }}">
        without the route handler threading it through manually."""
        if hasattr(request, "session"):
            return {"csrf_token": csrf_service.mint_token(request.session)}
        return {"csrf_token": ""}

    app.state.templates = Jinja2Templates(
        directory=templates_dir,
        context_processors=[_csrf_context],
    )

    # Jinja global: live disk status chip used by parent/base.html topnav.
    # Reads free space fresh on every render so the chip is always current.
    from app.services.storage_report import get_disk_status_brief

    app.state.templates.env.globals["disk_status"] = lambda: get_disk_status_brief(
        config.data_dir, config.storage.min_free_disk_bytes
    )

    # Jinja global: iframe-safe embed URL for a given extractor +
    # raw video ID. Returns None for platforms that don't have a
    # clean embed, which the parent content_preview.html template
    # uses as the "show link-out fallback" signal.
    from app.services.embeds import embed_url_for
    app.state.templates.env.globals["embed_url_for"] = embed_url_for

    # Jinja global: build the composite "{extractor}_{raw}" storage
    # key from a raw yt-dlp ID. Used by content_preview.html to
    # point at /media/thumb/<composite> without re-implementing the
    # sanitisation rules in Jinja.
    from app.services.ids import make_video_id
    app.state.templates.env.globals["make_video_id"] = make_video_id

    # Jinja filter: parse a data_json string safely. Used by parent stats
    # to render friendly labels for events like channel_created.
    import json as _json

    def _fromjson(raw):
        if not raw:
            return {}
        try:
            return _json.loads(raw)
        except Exception:
            return {}

    app.state.templates.env.filters["fromjson"] = _fromjson

    # Static files
    static_dir = os.path.join(os.path.dirname(__file__), "static")
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    # First-run redirect
    @app.middleware("http")
    async def first_run_redirect(request: Request, call_next):
        path = request.url.path
        if (config.is_first_run
                and not path.startswith("/parent/setup")
                and not path.startswith("/static")
                # Operational endpoints must answer even before setup;
                # they exist precisely so an operator can confirm the
                # process is alive on a fresh install.
                and not path.startswith("/healthz")):
            return RedirectResponse(url="/parent/setup", status_code=302)
        return await call_next(request)

    # Anonymous-kid redirect: when a logged-out visitor lands on any
    # kid-facing page (home, watch, channel, search, upload), bounce
    # them to /profiles so they can pick a kid profile. Single-profile
    # households get auto-selected there and land back on /; multi
    # profile or PIN-protected households get the picker. This makes
    # the "log out from parent, test as kid" flow work without the
    # user having to know about /profiles.
    _KID_PATH_PREFIXES = ("/watch/", "/channel/", "/search", "/upload")

    @app.middleware("http")
    async def anonymous_kid_redirect(request: Request, call_next):
        if request.method != "GET":
            return await call_next(request)
        path = request.url.path
        # Only redirect truly anonymous sessions — parent or kid
        # logged in skip through.
        session = request.session
        if session.get("parent_authenticated") or session.get("profile_id"):
            return await call_next(request)
        # Kid home is exactly "/". Everything else is matched by prefix.
        is_kid_page = path == "/" or any(path.startswith(p) for p in _KID_PATH_PREFIXES)
        if not is_kid_page:
            return await call_next(request)
        return RedirectResponse(url="/profiles", status_code=302)

    # SessionMiddleware must wrap the @middleware decorators above so
    # request.session is populated before they run. See the comment
    # near RequestLoggingMiddleware above for the Starlette ordering
    # rules that force this late registration.
    app.add_middleware(CSRFMiddleware, csrf_service=csrf_service)

    app.add_middleware(
        SessionMiddleware,
        secret_key=config.parent.session_secret,
        max_age=config.parent.session_timeout_hours * 3600,
        # same_site='strict' defeats nearly all CSRF vectors on its
        # own: a malicious cross-origin POST cannot include the session
        # cookie. Curatables is a LAN-only app with no legitimate
        # cross-site POSTs, so strict is safe. CSRFMiddleware above
        # layers on defense-in-depth.
        same_site="strict",
    )

    # Prometheus instrumentation goes outermost so the counter
    # observes the final status code returned to the client (after
    # exception handlers and middleware short-circuits). When
    # prometheus_enabled=False the middleware skips its work in O(1).
    app.add_middleware(PrometheusMiddleware, metrics=app.state.metrics)

    # --- Exception handlers ---
    import logging
    _error_logger = logging.getLogger("curatables.errors")

    @app.exception_handler(NotAuthenticated)
    async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
        return RedirectResponse(url="/parent/login", status_code=302)

    @app.exception_handler(NotAChild)
    async def not_a_child_handler(request: Request, exc: NotAChild):
        return RedirectResponse(url="/profiles", status_code=302)

    @app.exception_handler(Exception)
    async def global_error_handler(request: Request, exc: Exception):
        _error_logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        from starlette.responses import HTMLResponse
        if request.url.path.startswith("/parent/"):
            try:
                return app.state.templates.TemplateResponse(request,
                    "parent/error.html", {"request": request}, status_code=500)
            except Exception:
                pass
        try:
            tpl_path = os.path.join(os.path.dirname(__file__), "templates", "base", "kid", "error.html")
            with open(tpl_path) as f:
                return HTMLResponse(content=f.read(), status_code=500)
        except Exception:
            return HTMLResponse(content="<h1>Something went wrong</h1>", status_code=500)

    # --- Wire feature routers ---
    from app.features.parent_auth.router import router as auth_router
    from app.features.parent_dashboard.router import router as dashboard_router
    from app.features.parent_content.router import router as content_router
    from app.features.parent_settings.router import router as settings_router
    from app.features.parent_stats.router import router as stats_router
    from app.features.parent_storage.router import router as parent_storage_router
    from app.features.parent_uploads.router import router as parent_uploads_router
    from app.features.kid_uploads.router import router as kid_uploads_router
    from app.features.parent_channels.router import router as parent_channels_router
    from app.features.parent_sharing.router import router as parent_sharing_router
    from app.features.parent_profiles.router import router as parent_profiles_router
    from app.features.kid_profiles.router import router as kid_profiles_router
    from app.features.kid_browse.router import router as browse_router
    from app.features.kid_watch.router import router as watch_router
    from app.features.kid_search.router import router as kid_search_router
    from app.features.kid_comments.router import router as kid_comments_router
    from app.features.media.router import router as media_router
    from app.features.api.router import router as api_router
    from app.features.healthz.router import router as healthz_router

    app.include_router(healthz_router)
    app.include_router(auth_router)
    app.include_router(dashboard_router)
    app.include_router(content_router)
    app.include_router(settings_router)
    app.include_router(stats_router)
    app.include_router(parent_storage_router)
    app.include_router(parent_uploads_router)
    # parent_sharing must be included BEFORE parent_channels because
    # both routers mount under /parent/channels/; the sharing router
    # defines /import + /{id}/export, while parent_channels has a
    # catch-all /{channel_id}/edit that would otherwise swallow
    # "/import" as a channel_id=42 lookup.
    app.include_router(parent_sharing_router)
    app.include_router(parent_channels_router)
    app.include_router(parent_profiles_router)
    app.include_router(kid_profiles_router)
    app.include_router(browse_router)
    app.include_router(watch_router)
    app.include_router(kid_search_router)
    app.include_router(kid_comments_router)
    app.include_router(kid_uploads_router)
    app.include_router(media_router)
    app.include_router(api_router)

    return app
