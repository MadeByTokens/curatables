"""Parent settings: config, password change."""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse

from app.dependencies import (
    require_parent, get_auth_service, get_config, get_relocation_service,
)
from app.services.auth import AuthService
from app.services.relocation import RelocationService, RelocationError
from app.config import Config, ensure_directories
from app.models import ViewerContext

router = APIRouter(prefix="/parent", tags=["parent-settings"])


@router.get("/help", response_class=HTMLResponse)
def help_page(request: Request,
              viewer: ViewerContext = Depends(require_parent)):
    return request.app.state.templates.TemplateResponse(request, "parent/help.html", {
        "request": request,
    })


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request,
                  viewer: ViewerContext = Depends(require_parent),
                  config: Config = Depends(get_config)):
    return request.app.state.templates.TemplateResponse(request, "parent/settings.html", {
        "request": request, "config": config, "success": None, "error": None,
        "advanced_open": False,
    })


@router.post("/settings")
def settings_save(request: Request,
                  data_dir: str = Form(...),
                  cache_days: int = Form(...),
                  default_resolution: str = Form(...),
                  subtitle_langs: str = Form("en"),
                  viewer: ViewerContext = Depends(require_parent),
                  config: Config = Depends(get_config)):
    templates = request.app.state.templates
    ctx = {"request": request, "config": config, "success": None, "advanced_open": False}

    data_dir = data_dir.strip()
    if not data_dir:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Data directory is required."})

    cache_days = max(0, cache_days)
    config.storage.path = data_dir
    config.storage.cache_days = cache_days
    if default_resolution in ("360p", "480p", "720p", "1080p"):
        config.storage.default_resolution = default_resolution
    config.storage.subtitle_langs = subtitle_langs.strip()
    try:
        ensure_directories(config)
    except OSError as e:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": f"Cannot create data directory: {e}"})
    config.save()
    return templates.TemplateResponse(request, "parent/settings.html", {
        **ctx, "success": "Settings saved.", "error": None,
    })


@router.post("/settings/advanced")
def settings_save_advanced(request: Request,
                           port: int = Form(...),
                           host: str = Form(...),
                           default_mode: str = Form("cache"),
                           min_free_disk_gb: float = Form(2.0),
                           max_upload_gb: float = Form(10.0),
                           max_kid_upload_gb: float = Form(0.5),
                           impersonate: str = Form("chrome"),
                           cookies_file: str = Form(""),
                           cookies_from_browser: str = Form(""),
                           session_timeout_hours: int = Form(24),
                           log_level: str = Form("info"),
                           viewer: ViewerContext = Depends(require_parent),
                           config: Config = Depends(get_config)):
    templates = request.app.state.templates
    ctx = {"request": request, "config": config, "success": None, "advanced_open": True}

    # Validation
    if not (1 <= port <= 65535):
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Port must be between 1 and 65535."})
    host = host.strip()
    if not host or " " in host:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Host address is required and must not contain spaces."})
    if default_mode not in ("cache", "library"):
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Invalid storage mode."})
    if min_free_disk_gb < 0:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Minimum free disk space cannot be negative."})
    if max_upload_gb < 0:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Maximum upload size cannot be negative."})
    if max_kid_upload_gb < 0:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Maximum kid upload size cannot be negative."})
    if session_timeout_hours < 1:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Session timeout must be at least 1 hour."})
    if log_level not in ("debug", "info", "warning", "error"):
        log_level = "info"

    config.server.port = port
    config.server.host = host
    config.server.log_level = log_level
    config.storage.default_mode = default_mode
    config.storage.min_free_disk_bytes = int(min_free_disk_gb * 1_073_741_824)
    config.storage.max_upload_bytes = int(max_upload_gb * 1_073_741_824)
    config.storage.max_kid_upload_bytes = int(max_kid_upload_gb * 1_073_741_824)
    config.storage.impersonate = impersonate.strip()
    config.storage.cookies_file = cookies_file.strip()
    config.storage.cookies_from_browser = cookies_from_browser.strip()
    config.parent.session_timeout_hours = session_timeout_hours
    config.save()
    return templates.TemplateResponse(request, "parent/settings.html", {
        **ctx, "success": "Advanced settings saved. Restart the server for port, host, session timeout, and log level changes to take effect.",
        "error": None,
    })


@router.post("/settings/password")
def change_password(request: Request,
                    current_password: str = Form(...),
                    new_password: str = Form(...),
                    new_password2: str = Form(...),
                    viewer: ViewerContext = Depends(require_parent),
                    auth: AuthService = Depends(get_auth_service),
                    config: Config = Depends(get_config)):
    templates = request.app.state.templates
    ctx = {"request": request, "config": config, "success": None, "advanced_open": False}

    if not auth.verify_password(current_password):
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Current password is incorrect."})
    if len(new_password) < 4:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "New password must be at least 4 characters."})
    if new_password != new_password2:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "New passwords do not match."})

    auth.set_password(new_password)
    return templates.TemplateResponse(request, "parent/settings.html", {
        **ctx, "success": "Password changed.", "error": None})


@router.post("/settings/move-data")
def settings_move_data(request: Request,
                       new_data_dir: str = Form(...),
                       viewer: ViewerContext = Depends(require_parent),
                       relocation: RelocationService = Depends(get_relocation_service),
                       config: Config = Depends(get_config)):
    templates = request.app.state.templates
    ctx = {"request": request, "config": config, "success": None,
           "error": None, "advanced_open": True}
    try:
        new_path = relocation.move(new_data_dir)
    except RelocationError as e:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "move_data_error": str(e),
        })
    return templates.TemplateResponse(request, "parent/settings.html", {
        **ctx,
        "move_data_success": (
            f"Data directory moved to {new_path}. The server is still "
            f"running from the new location. A restart (Ctrl-C then "
            f"`python run.py`) is recommended at your convenience but "
            f"not required."
        ),
    })
