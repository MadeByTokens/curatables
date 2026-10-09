"""FastAPI application factory."""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.responses import PlainTextResponse, RedirectResponse

from app.config import load_config, ensure_directories
from app.dependencies import FeatureDisabled, NotAuthenticated, NotAChild
from app.lifecycle import make_lifespan, prepare_data_dir, startup_jobs
from app.logging_config import setup_logging
from app.middleware import (
    RequestLoggingMiddleware, BodySizeLimitMiddleware, RequestIDMiddleware,
    CSRFMiddleware, PrometheusMiddleware,
)
from app.services.auth import AuthService
from app.services.csrf import CSRFService
from app.services.metrics import MetricsService


CONTENT_SECURITY_POLICY = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; "
    "frame-src https:; object-src 'none'; base-uri 'self'; form-action 'self'; "
    "frame-ancestors 'none'"
)
"""Pages run only the app's own scripts. Inline style attributes stay
allowed (templates set per-channel colours); frames are https only, for
the video-site embeds in the parent preview (PeerTube can be any host)."""


def create_app(data_dir: str | None = None, log_level: str | None = None) -> FastAPI:
    config = load_config(data_dir)
    ensure_directories(config)

    # Initialize logging — CLI log_level overrides config
    effective_log_level = log_level or config.server.log_level
    log_dir = config.data_dir / "logs"
    setup_logging(effective_log_level, log_dir)

    prepare_data_dir(config)
    metrics = MetricsService(enabled=config.server.prometheus_enabled)
    startup_jobs(config, metrics)

    app = FastAPI(title="curatables", docs_url=None, redoc_url=None,
                  lifespan=make_lifespan(config))

    app.add_middleware(RequestLoggingMiddleware)
    # RequestIDMiddleware stamps each request with a correlation ID in a
    # contextvar. The logging filter injects it into every log record so
    # a grep of the logs can follow a single request across modules.
    # Added OUTSIDE RequestLoggingMiddleware (via later add_middleware
    # ordering) so the logging middleware itself sees the set ID.
    app.add_middleware(RequestIDMiddleware)
    # SessionMiddleware is added below, AFTER the @app.middleware("http")
    # decorators. Starlette builds the middleware stack by inserting each
    # new add_middleware at index 0 and then wrapping outside-in in
    # reversed order, so the LAST add_middleware call becomes the
    # outermost wrapper and runs first on an incoming request. We need
    # SessionMiddleware to run before the @middleware decorators so that
    # request.session is populated when the anonymous_kid_redirect
    # middleware reads it.

    app.state.config = config
    app.state.data_dir_pinned = data_dir is not None

    # Process start time used by /healthz to report uptime. monotonic()
    # is immune to wall-clock jumps, which matters for an appliance that
    # stays up for months at a time.
    import time as _time
    app.state.start_time = _time.monotonic()

    # Prometheus metrics service. Always instantiated; the recorders
    # no-op when prometheus_enabled=False so call sites don't branch.
    # /metrics returns 404 when disabled.
    app.state.metrics = metrics

    # CSRF service — shared between CSRFMiddleware (validates) and the
    # Jinja csrf_token context processor (mints). Created here, ahead
    # of the Jinja2Templates constructor that closes over it.
    session_secret = config.parent.session_secret
    if not session_secret:
        raise RuntimeError("prepare_data_dir() did not set a session secret")
    csrf_service = CSRFService(
        secret_key=session_secret,
        max_age_seconds=config.parent.session_timeout_hours * 3600,
    )
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
    from app.template_utils import register_filters
    register_filters(app.state.templates)

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
    from app.services.qr import qr_available
    app.state.templates.env.globals["qr_available"] = qr_available
    from app.web import kid_address
    app.state.templates.env.globals["kid_address"] = kid_address

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

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Content-Security-Policy", CONTENT_SECURITY_POLICY)
        # YouTube embeds in the parent preview need an origin referrer.
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        return response

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
        if session.get("profile_id") or AuthService(app.state.config).is_parent_session(session):
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
        secret_key=session_secret,
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

    # Outermost of all: reject oversized bodies before any other layer
    # (CSRF parses form bodies) can read them into memory.
    app.add_middleware(
        BodySizeLimitMiddleware,
        upload_limit=lambda: config.storage.max_upload_bytes,
        kid_upload_limit=lambda: min(config.storage.max_upload_bytes,
                                     config.storage.max_kid_upload_bytes),
    )

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

    from fastapi.exceptions import RequestValidationError
    from fastapi.exception_handlers import (
        http_exception_handler, request_validation_exception_handler,
    )
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import HTMLResponse

    def _error_page(request: Request, status_code: int, heading: str | None = None):
        if request.url.path.startswith("/parent/"):
            try:
                return app.state.templates.TemplateResponse(request,
                    "parent/error.html", {"request": request, "heading": heading},
                    status_code=status_code)
            except Exception:
                _error_logger.exception("Parent error page failed to render")
        try:
            tpl_path = os.path.join(os.path.dirname(__file__), "templates", "base", "kid", "error.html")
            with open(tpl_path) as f:
                return HTMLResponse(content=f.read(), status_code=status_code)
        except Exception:
            return HTMLResponse(content="<h1>Something went wrong</h1>", status_code=status_code)

    def _wants_html(request: Request) -> bool:
        path = request.url.path
        if path.startswith(("/api/", "/media/", "/healthz", "/metrics", "/parent/uploads/tus")):
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
