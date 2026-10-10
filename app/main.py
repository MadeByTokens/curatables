"""FastAPI application factory."""

import json
import logging
import os
import time

from fastapi import FastAPI, Request
from fastapi.exception_handlers import (
    http_exception_handler, request_validation_exception_handler,
)
from fastapi.exceptions import RequestValidationError
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import HTMLResponse, PlainTextResponse, RedirectResponse

from app.config import load_config, ensure_directories
from app.db.connection import create_connection, get_db_path
from app.dependencies import FeatureDisabled, NotAuthenticated, NotAChild
from app.features.api.router import router as api_router
from app.features.healthz.router import router as healthz_router
from app.features.kid_browse.router import router as browse_router
from app.features.kid_comments.router import router as kid_comments_router
from app.features.kid_profiles.router import router as kid_profiles_router
from app.features.kid_search.router import router as kid_search_router
from app.features.kid_uploads.router import router as kid_uploads_router
from app.features.kid_watch.router import router as watch_router
from app.features.kid_wishlist.router import router as kid_wishlist_router
from app.features.media.router import router as media_router
from app.features.parent_auth.router import router as auth_router
from app.features.parent_channels.router import router as parent_channels_router
from app.features.parent_content.router import router as content_router
from app.features.parent_dashboard.router import router as dashboard_router
from app.features.parent_import.router import router as parent_import_router
from app.features.parent_profiles.router import router as parent_profiles_router
from app.features.parent_requests.router import router as parent_requests_router
from app.features.parent_settings.router import router as settings_router
from app.features.parent_sharing.router import router as parent_sharing_router
from app.features.parent_stats.router import router as stats_router
from app.features.parent_storage.router import router as parent_storage_router
from app.features.parent_system.router import BOX_NOTICES, router as parent_system_router
from app.features.parent_uploads.router import router as parent_uploads_router
from app.lifecycle import make_lifespan, prepare_data_dir, startup_jobs
from app.logging_config import setup_logging
from app.middleware import (
    BodySizeLimitMiddleware, CSRFMiddleware, HostCheckMiddleware, PrometheusMiddleware,
    RequestIDMiddleware, RequestLoggingMiddleware,
)
from app.services.auth import AuthService
from app.services.csrf import CSRFService
from app.services.embeds import embed_url_for
from app.services.ids import make_video_id
from app.services.media_fs import capped, media_missing
from app.services.metrics import MetricsService
from app.services.qr import qr_available
from app.services.search_gate import GATE
from app.services.storage_report import get_disk_status_brief
from app.services.system_helper import SystemHelper
from app.template_utils import register_filters
from app.web import kid_address

_APP_DIR = os.path.dirname(__file__)
_KID_ERROR_PAGE = os.path.join(_APP_DIR, "templates", "base", "kid", "error.html")

CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
    "frame-src https:; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)
"""Pages run only the app's own scripts. Inline style attributes stay
allowed (templates set per-channel colours); frames are https only, for
the video-site embeds in the parent preview (PeerTube can be any host)."""


def _fromjson(raw):
    """Jinja filter: an event's data_json as a dict, {} when unreadable."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def create_app(data_dir: str | None = None, log_level: str | None = None) -> FastAPI:
    config = load_config(data_dir)
    ensure_directories(config)
    setup_logging(log_level or config.server.log_level, config.data_dir / "logs")

    prepare_data_dir(config)
    GATE.use_file(get_db_path(config.data_dir).with_name("cache.db"))
    metrics = MetricsService(enabled=config.server.prometheus_enabled)
    startup_jobs(config, metrics)

    app = FastAPI(title="curatables", docs_url=None, redoc_url=None,
                  lifespan=make_lifespan(config))

    app.state.config = config
    app.state.data_dir_pinned = data_dir is not None
    # Background threads (imports) open their own connections through this.
    app.state.db_connect = lambda: create_connection(get_db_path(config.data_dir))
    app.state.import_worker = None
    # monotonic(): the /healthz uptime must not jump with the wall clock.
    app.state.start_time = time.monotonic()
    app.state.metrics = metrics

    session_secret = config.parent.session_secret
    if not session_secret:
        raise RuntimeError("prepare_data_dir() did not set a session secret")
    # Shared by CSRFMiddleware (validates) and the template context (mints).
    csrf_service = CSRFService(
        secret_key=session_secret,
        max_age_seconds=config.parent.session_timeout_hours * 3600,
    )
    app.state.csrf = csrf_service
    app.state.system_helper = SystemHelper(config.server.helper_socket)

    def _csrf_context(request):
        """Every template gets a csrf_token for its forms."""
        if hasattr(request, "session"):
            return {"csrf_token": csrf_service.mint_token(request.session)}
        return {"csrf_token": ""}

    templates = Jinja2Templates(
        directory=os.path.join(_APP_DIR, "templates", "base"),
        context_processors=[_csrf_context],
    )
    app.state.templates = templates
    register_filters(templates)
    templates.env.globals["box_notices"] = BOX_NOTICES
    templates.env.globals["media_missing"] = lambda: media_missing(config.storage.media_path)
    # Read on every render, so the free-space chip in the parent topnav is current.
    templates.env.globals["disk_status"] = lambda: get_disk_status_brief(
        config.media_dir, config.storage.min_free_disk_bytes, config.storage.low_space_percent
    )
    templates.env.globals["embed_url_for"] = embed_url_for
    templates.env.globals["qr_available"] = qr_available
    templates.env.globals["kid_address"] = kid_address
    templates.env.globals["make_video_id"] = make_video_id
    templates.env.filters["fromjson"] = _fromjson

    app.mount("/static", StaticFiles(directory=os.path.join(_APP_DIR, "static")), name="static")

    # Middleware order: each add_middleware() (and @app.middleware) wraps
    # everything added before it, so the one added last sees a request
    # first. From the outside in:
    #   HostCheck, BodySizeLimit, Prometheus, Session, CSRF,
    #   the @app.middleware functions, RequestID, RequestLogging.
    # BodySizeLimit must stay outside CSRF (which reads form bodies) and
    # Session outside everything that reads request.session.
    app.add_middleware(RequestLoggingMiddleware)
    app.add_middleware(RequestIDMiddleware)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        # YouTube embeds in the parent preview need an origin referrer.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

    @app.middleware("http")
    async def first_run_redirect(request: Request, call_next):
        path = request.url.path
        if (config.is_first_run
                and not path.startswith("/parent/setup")
                and not path.startswith("/static")
                # An operator must be able to check that a fresh install is up.
                and not path.startswith("/healthz")):
            return RedirectResponse(url="/parent/setup", status_code=302)
        return await call_next(request)

    # First-run step 2: on the Pi image the Linux login password must be
    # chosen before the rest of the dashboard opens.
    _STEP2_OPEN = ("/parent/setup", "/parent/login", "/parent/logout")

    @app.middleware("http")
    async def system_setup_redirect(request: Request, call_next):
        path = request.url.path
        if (path.startswith("/parent") and not path.startswith(_STEP2_OPEN)
                and not config.is_first_run
                and not config.parent.system_setup_done
                and app.state.system_helper.available):
            return RedirectResponse(url="/parent/setup/system", status_code=302)
        return await call_next(request)

    # A visitor with neither a kid profile nor a parent login who opens a
    # kid page goes to /profiles, which auto-selects the only profile of a
    # single-kid household or shows the picker.
    _KID_PATH_PREFIXES = ("/watch/", "/channel/", "/search", "/upload")

    @app.middleware("http")
    async def anonymous_kid_redirect(request: Request, call_next):
        if request.method != "GET":
            return await call_next(request)
        path = request.url.path
        session = request.session
        if session.get("profile_id") or AuthService(app.state.config).is_parent_session(session):
            return await call_next(request)
        is_kid_page = path == "/" or any(path.startswith(p) for p in _KID_PATH_PREFIXES)
        if not is_kid_page:
            return await call_next(request)
        return RedirectResponse(url="/profiles", status_code=302)

    # Videos drive missing: kids get a calm "videos are resting" screen
    # instead of broken tiles; the parent dashboard keeps working so the
    # drive can be reconnected from Box, Drives.
    _KID_OPEN = ("/parent", "/static", "/healthz", "/metrics", "/api", "/media")

    @app.middleware("http")
    async def media_missing_screen(request: Request, call_next):
        path = request.url.path
        if not path.startswith(_KID_OPEN) and media_missing(config.storage.media_path):
            if request.method != "GET":
                return PlainTextResponse("The videos drive is not connected.", status_code=503)
            return app.state.templates.TemplateResponse(
                request, "kid/resting.html", {"request": request}, status_code=503)
        return await call_next(request)

    app.add_middleware(CSRFMiddleware, csrf_service=csrf_service)

    app.add_middleware(
        SessionMiddleware,
        secret_key=session_secret,
        max_age=config.parent.session_timeout_hours * 3600,
        # A cross-site POST cannot carry the session cookie at all, and the
        # app has no legitimate cross-site requests. CSRFMiddleware is
        # defence in depth.
        same_site="strict",
    )

    # Wraps the session, CSRF and app layers, so it counts the status
    # code they finally return. A no-op while prometheus_enabled is off.
    app.add_middleware(PrometheusMiddleware, metrics=metrics)

    app.add_middleware(
        BodySizeLimitMiddleware,
        upload_limit=lambda: capped(config.media_dir, config.storage.max_upload_bytes),
        kid_upload_limit=lambda: capped(config.media_dir, min(config.storage.max_upload_bytes,
                                                              config.storage.max_kid_upload_bytes)),
    )

    # Answer only to the box's own names and IP addresses (DNS rebinding).
    app.add_middleware(HostCheckMiddleware, extra_hosts=lambda: config.server.allowed_hosts)

    # --- Exception handlers ---
    _error_logger = logging.getLogger("curatables.errors")

    @app.exception_handler(NotAuthenticated)
    async def not_authenticated_handler(request: Request, exc: NotAuthenticated):
        return RedirectResponse(url="/parent/login", status_code=302)

    @app.exception_handler(NotAChild)
    async def not_a_child_handler(request: Request, exc: NotAChild):
        return RedirectResponse(url="/profiles", status_code=302)

    @app.exception_handler(FeatureDisabled)
    async def feature_disabled_handler(request: Request, exc: FeatureDisabled):
        if request.method == "GET":
            return RedirectResponse(url="/", status_code=302)
        return PlainTextResponse("", status_code=403)

    def _error_page(request: Request, status_code: int, heading: str | None = None):
        if request.url.path.startswith("/parent/"):
            try:
                return app.state.templates.TemplateResponse(request,
                    "parent/error.html", {"request": request, "heading": heading},
                    status_code=status_code)
            except Exception:
                _error_logger.exception("Parent error page failed to render")
        try:
            with open(_KID_ERROR_PAGE) as f:
                return HTMLResponse(content=f.read(), status_code=status_code)
        except Exception:
            return HTMLResponse(content="<h1>Something went wrong</h1>", status_code=status_code)

    def _wants_html(request: Request) -> bool:
        path = request.url.path
        if path.startswith(("/api/", "/media/", "/healthz", "/metrics", "/parent/upload/tus")):
            return False
        return "text/html" in request.headers.get("accept", "") or request.method == "GET"

    @app.exception_handler(StarletteHTTPException)
    async def http_error_handler(request: Request, exc: StarletteHTTPException):
        if exc.status_code in (404, 405) and _wants_html(request):
            return _error_page(request, exc.status_code, "Page not found")
        return await http_exception_handler(request, exc)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(request: Request, exc: RequestValidationError):
        if _wants_html(request):
            return _error_page(request, 400, "That request didn't look right")
        return await request_validation_exception_handler(request, exc)

    @app.exception_handler(Exception)
    async def global_error_handler(request: Request, exc: Exception):
        _error_logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _error_page(request, 500)

    for router in (
        healthz_router, auth_router, dashboard_router, content_router, settings_router,
        stats_router, parent_storage_router, parent_system_router, parent_uploads_router,
        parent_sharing_router, parent_channels_router, parent_profiles_router,
        parent_requests_router, parent_import_router, kid_profiles_router, browse_router,
        watch_router, kid_search_router, kid_wishlist_router, kid_comments_router,
        kid_uploads_router, media_router, api_router,
    ):
        app.include_router(router)

    return app
