"""Parent settings: config, password change."""

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse

from app.i18n import LANGUAGE_CODES
from app.dependencies import (
    require_parent, get_auth_service, get_config, get_relocation_service,
)
from app.services.auth import AuthService, MIN_PASSWORD_LENGTH
from app.services.relocation import RelocationService, RelocationError
from app.config import Config
from app.services.storage import RESOLUTION_MAP
from app.models import ViewerContext
from app.services import updates

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
        "advanced_open": False, "update_state": updates.update_state(config),
    })


@router.post("/settings/update")
def settings_update(request: Request,
                    viewer: ViewerContext = Depends(require_parent),
                    config: Config = Depends(get_config)):
    """Request a yt-dlp update.

    The app cannot pip-install or restart itself from inside its sandbox
    (see app/services/updates.py for why). It only drops a request flag
    into the data dir; the root-owned curatables-updater path-unit does
    the privileged work and writes a result the page shows on reload.
    """
    templates = request.app.state.templates
    ctx = {"request": request, "config": config, "advanced_open": False,
           "error": None}
    try:
        updates.request_update(config, kind=updates.KIND_YTDLP)
    except OSError as e:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "success": None, "update_state": updates.update_state(config),
            "error": f"Could not write the update request: {e}"})
    return templates.TemplateResponse(request, "parent/settings.html", {
        **ctx, "success": (
            "Update requested. The updater runs in the background and the "
            "server will restart when it finishes — reload this page in a "
            "moment to see the result."
        ),
        "update_state": updates.update_state(config),
    })


@router.post("/settings")
def settings_save(request: Request,
                  cache_days: int = Form(...),
                  default_resolution: str = Form(...),
                  subtitle_langs: str = Form("en"),
                  keep_forever_default: str = Form(""),
                  kid_language: str = Form(""),
                  viewer: ViewerContext = Depends(require_parent),
                  config: Config = Depends(get_config)):
    templates = request.app.state.templates
    ctx = {"request": request, "config": config, "success": None, "advanced_open": False}

    config.storage.cache_days = max(0, cache_days)
    if default_resolution in RESOLUTION_MAP:
        config.storage.default_resolution = default_resolution
    config.storage.subtitle_langs = subtitle_langs.strip()
    config.storage.default_mode = "library" if keep_forever_default == "1" else "cache"
    if kid_language in LANGUAGE_CODES:
        config.server.kid_language = kid_language
    config.save()
    return templates.TemplateResponse(request, "parent/settings.html", {
        **ctx, "success": "Settings saved.", "error": None,
    })


def _lines(text: str, commas: bool = False) -> list[str]:
    seen: list[str] = []
    for part in (text.replace(",", "\n") if commas else text).splitlines():
        part = " ".join(part.split())
        if part and part.lower() not in (s.lower() for s in seen):
            seen.append(part)
    return seen


@router.post("/settings/wishlist")
def settings_save_wishlist(request: Request,
                           blocked_words: str = Form(""),
                           allowed_channels: str = Form(""),
                           max_duration_minutes: int = Form(30),
                           max_results: int = Form(12),
                           viewer: ViewerContext = Depends(require_parent),
                           config: Config = Depends(get_config)):
    config.wishlist.blocked_words = _lines(blocked_words, commas=True)
    config.wishlist.allowed_channels = _lines(allowed_channels)
    config.wishlist.max_duration_minutes = max(0, min(max_duration_minutes, 600))
    config.wishlist.max_results = max(1, min(max_results, 30))
    config.save()
    return request.app.state.templates.TemplateResponse(request, "parent/settings.html", {
        "request": request, "config": config, "advanced_open": False,
        "success": "Video request rules saved.", "error": None,
        "update_state": updates.update_state(config),
    })


@router.post("/settings/imports")
def settings_save_imports(request: Request,
                          import_roots: str = Form(""),
                          lookups_enabled: str = Form(""),
                          tmdb_api_key: str = Form(""),
                          viewer: ViewerContext = Depends(require_parent),
                          config: Config = Depends(get_config)):
    roots, error = [], None
    for line in _lines(import_roots):
        path = Path(line).expanduser()
        if not path.is_absolute():
            error = f"{line} is not a full path (it must start with /)."
            break
        roots.append(str(path))
    if error is None:
        config.storage.import_roots = roots
        config.imports.lookups_enabled = lookups_enabled == "1"
        config.imports.tmdb_api_key = tmdb_api_key.strip()[:400]
        config.save()
    return request.app.state.templates.TemplateResponse(request, "parent/settings.html", {
        "request": request, "config": config, "advanced_open": False,
        "success": None if error else "Import settings saved.", "error": error,
        "update_state": updates.update_state(config),
    })


@router.post("/settings/advanced")
def settings_save_advanced(request: Request,
                           port: int = Form(...),
                           host: str = Form(...),
                           min_free_disk_gb: float = Form(2.0),
                           max_download_gb: float = Form(4.0),
                           max_concurrent_downloads: int = Form(1),
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
    if max_download_gb <= 0:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Maximum download size must be above zero."})
    if not (1 <= max_concurrent_downloads <= 4):
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "Downloads at once must be between 1 and 4."})
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
    config.storage.max_download_bytes = int(max_download_gb * 1_073_741_824)
    config.storage.max_concurrent_downloads = max_concurrent_downloads
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
    if len(new_password) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx,
            "error": f"New password must be at least {MIN_PASSWORD_LENGTH} characters."})
    if new_password != new_password2:
        return templates.TemplateResponse(request, "parent/settings.html", {
            **ctx, "error": "New passwords do not match."})

    auth.set_password(new_password)
    auth.start_parent_session(request.session)
    return templates.TemplateResponse(request, "parent/settings.html", {
        **ctx, "success": "Password changed. Other devices have been signed out.",
        "error": None})


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
