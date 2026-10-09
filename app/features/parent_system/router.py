"""The Box pages: the Linux login, SSH, Wi-Fi, restarting, and the
progress page for the system helper's background tasks."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from app.config import Config
from app.dependencies import get_auth_service, get_config, get_system_helper, require_parent
from app.models import ViewerContext
from app.services.auth import MIN_PASSWORD_LENGTH, AuthService
from app.services.system_helper import (
    SAME_AS_PARENT, HelperFailed, HelperUnavailable, SystemHelper,
)

router = APIRouter(prefix="/parent/system", tags=["parent-system"])

SAFE_BACK = ("/parent/system", "/parent/import")


def _try(helper: SystemHelper, action: str, **params):
    """(result, error message)"""
    try:
        return helper.call(action, **params), None
    except (HelperUnavailable, HelperFailed) as e:
        return None, str(e)


def unavailable_page(request: Request) -> Response:
    page: Response = request.app.state.templates.TemplateResponse(
        request, "parent/system_unavailable.html", {"request": request}, status_code=200)
    return page


def job_redirect(job: dict, back: str) -> RedirectResponse:
    return RedirectResponse(url=f"/parent/system/jobs/{job['id']}?back={quote(back)}", status_code=303)


def _overview(request: Request, helper: SystemHelper, *, success: str | None = None,
              error: str | None = None, networks: list | None = None, form: dict | None = None,
              status_code: int = 200) -> Response:
    if not helper.available:
        return unavailable_page(request)
    account, account_error = _try(helper, "account_status")
    wifi, wifi_error = _try(helper, "wifi_status")
    page: Response = request.app.state.templates.TemplateResponse(request, "parent/system.html", {
        "request": request, "account": account, "wifi": wifi,
        "load_error": account_error or wifi_error, "success": success, "error": error,
        "networks": networks, "form": form or {},
    }, status_code=status_code)
    return page


@router.get("", response_class=HTMLResponse)
def system_page(request: Request,
                viewer: ViewerContext = Depends(require_parent),
                helper: SystemHelper = Depends(get_system_helper)):
    return _overview(request, helper)


@router.post("/linux-password")
def change_linux_password(request: Request,
                          parent_password: str = Form(...),
                          password: str = Form(...),
                          password2: str = Form(...),
                          viewer: ViewerContext = Depends(require_parent),
                          auth: AuthService = Depends(get_auth_service),
                          helper: SystemHelper = Depends(get_system_helper)):
    if not auth.verify_password(parent_password):
        return _overview(request, helper, error="The parent password is incorrect.", status_code=400)
    if len(password) < MIN_PASSWORD_LENGTH:
        return _overview(request, helper, status_code=400,
                         error=f"The new password must be at least {MIN_PASSWORD_LENGTH} characters.")
    if password != password2:
        return _overview(request, helper, error="The new passwords do not match.", status_code=400)
    if auth.verify_password(password):
        return _overview(request, helper, error=SAME_AS_PARENT, status_code=400)
    result, error = _try(helper, "account_set_password", password=password)
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _overview(request, helper, success=f"The password for {result['user']} was changed.")


@router.post("/ssh")
def set_ssh(request: Request,
            parent_password: str = Form(...),
            enabled: str = Form(""),
            viewer: ViewerContext = Depends(require_parent),
            auth: AuthService = Depends(get_auth_service),
            helper: SystemHelper = Depends(get_system_helper)):
    if not auth.verify_password(parent_password):
        return _overview(request, helper, error="The parent password is incorrect.", status_code=400)
    result, error = _try(helper, "ssh_set", enabled=enabled == "1")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _overview(request, helper, success="SSH logins are now " + (
        "allowed." if result["ssh_enabled"] else "turned off."))


@router.get("/wifi/scan", response_class=HTMLResponse)
def wifi_scan(request: Request,
              viewer: ViewerContext = Depends(require_parent),
              helper: SystemHelper = Depends(get_system_helper)):
    result, error = _try(helper, "wifi_scan")
    if error:
        return _overview(request, helper, error=error)
    return _overview(request, helper, networks=result["networks"],
                     form={"ssid": request.query_params.get("ssid", "")})


@router.post("/wifi")
def set_wifi(request: Request,
             ssid: str = Form(...),
             password: str = Form(""),
             hidden: str = Form(""),
             country: str = Form(""),
             viewer: ViewerContext = Depends(require_parent),
             helper: SystemHelper = Depends(get_system_helper)):
    job, error = _try(helper, "wifi_set", ssid=ssid, password=password, hidden=hidden == "1",
                      country=country.strip())
    if error:
        return _overview(request, helper, error=error, status_code=400,
                         form={"ssid": ssid, "hidden": hidden == "1", "country": country})
    return job_redirect(job, "/parent/system")


def _waiting(request: Request, title: str, message: str, back: str, delay_ms: int = 6000) -> Response:
    page: Response = request.app.state.templates.TemplateResponse(request, "parent/system_wait.html", {
        "request": request, "title": title, "message": message, "back": back, "delay_ms": delay_ms})
    return page


@router.post("/restart")
def restart(request: Request,
            viewer: ViewerContext = Depends(require_parent),
            helper: SystemHelper = Depends(get_system_helper)):
    _result, error = _try(helper, "restart_curatables")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _waiting(request, "Restarting Curatables",
                    "This takes about half a minute. The page comes back by itself.", "/parent/system")


@router.post("/reboot")
def reboot(request: Request,
           viewer: ViewerContext = Depends(require_parent),
           helper: SystemHelper = Depends(get_system_helper)):
    _result, error = _try(helper, "reboot")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _waiting(request, "Restarting the box",
                    "This takes one to two minutes. The page comes back by itself.", "/parent/system",
                    delay_ms=25000)


@router.post("/network-restart")
def network_restart(request: Request,
                    viewer: ViewerContext = Depends(require_parent),
                    helper: SystemHelper = Depends(get_system_helper)):
    _result, error = _try(helper, "restart_network")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _waiting(request, "Reconnecting to the network",
                    "The connection drops for a few seconds. The page comes back by itself.",
                    "/parent/system")


def _safe_back(back: str) -> str:
    return back if back.startswith(SAFE_BACK) and "//" not in back else "/parent/system"


@router.get("/jobs/{job_id}.json")
def job_json(job_id: str,
             viewer: ViewerContext = Depends(require_parent),
             helper: SystemHelper = Depends(get_system_helper)):
    job, error = _try(helper, "job_status", id=job_id)
    if error:
        return JSONResponse({"state": "unknown", "error": error}, status_code=503)
    return JSONResponse(job)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str, back: str = "/parent/system",
             viewer: ViewerContext = Depends(require_parent),
             helper: SystemHelper = Depends(get_system_helper)):
    job, error = _try(helper, "job_status", id=job_id)
    return request.app.state.templates.TemplateResponse(request, "parent/system_job.html", {
        "request": request, "job": job, "error": error, "job_id": job_id, "back": _safe_back(back),
    })


# --- drives -------------------------------------------------------------------

DRIVES = "/parent/system/drives"


def _drives_page(request: Request, helper: SystemHelper, config: Config, *,
                 error: str | None = None, success: str | None = None, status_code: int = 200) -> Response:
    if not helper.available:
        return unavailable_page(request)
    state, load_error = _try(helper, "drives_list")
    backups, _backup_error = _try(helper, "backups_list")
    page: Response = request.app.state.templates.TemplateResponse(request, "parent/system_drives.html", {
        "request": request, "state": state, "backups": (backups or {}).get("backups", []),
        "error": error or load_error, "success": success, "config": config,
        "sd_free": _sd_free(config),
    }, status_code=status_code)
    return page


def _sd_free(config: Config) -> int:
    import shutil
    try:
        return shutil.disk_usage(config.data_dir).free
    except OSError:
        return 0


@router.get("/drives", response_class=HTMLResponse)
def drives_page(request: Request,
                viewer: ViewerContext = Depends(require_parent),
                helper: SystemHelper = Depends(get_system_helper),
                config: Config = Depends(get_config)):
    return _drives_page(request, helper, config)


def _drive_job(request: Request, helper: SystemHelper, config: Config, action: str,
               back: str = DRIVES, **params) -> Response:
    job, error = _try(helper, action, **params)
    if error:
        return _drives_page(request, helper, config, error=error, status_code=400)
    return job_redirect(job, back)


@router.post("/drives/use")
def drive_use(request: Request, device: str = Form(...),
              viewer: ViewerContext = Depends(require_parent),
              helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_use_for_videos", device=device)


@router.post("/drives/eject")
def drive_eject(request: Request, device: str = Form(...),
                viewer: ViewerContext = Depends(require_parent),
                helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_eject", device=device)


@router.post("/drives/reconnect")
def drive_reconnect(request: Request,
                    viewer: ViewerContext = Depends(require_parent),
                    helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_reconnect")


@router.post("/drives/repair")
def drive_repair(request: Request, device: str = Form(...),
                 viewer: ViewerContext = Depends(require_parent),
                 helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_repair", device=device)


@router.post("/drives/format")
def drive_format(request: Request, disk: str = Form(...), fstype: str = Form("exfat"),
                 label: str = Form("CURATABLES"), confirm: str = Form(""),
                 viewer: ViewerContext = Depends(require_parent),
                 helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_format", disk=disk, fstype=fstype,
                      label=label.strip(), confirm=confirm.strip())


@router.post("/drives/open")
def drive_open(request: Request, device: str = Form(...),
               viewer: ViewerContext = Depends(require_parent),
               helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_open_for_import", back="/parent/import", device=device)


@router.post("/drives/back-to-sd")
def drive_back_to_sd(request: Request,
                     viewer: ViewerContext = Depends(require_parent),
                     helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_back_to_sd")


@router.post("/drives/delete-sd-copy")
def drive_delete_sd_copy(request: Request,
                         viewer: ViewerContext = Depends(require_parent),
                         helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "drive_delete_sd_copy")


@router.post("/drives/backups")
def drive_backups_setting(request: Request, drive_backups: str = Form(""),
                          viewer: ViewerContext = Depends(require_parent),
                          helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    config.storage.drive_backups = drive_backups == "1"
    config.save()
    return _drives_page(request, helper, config, success=(
        "Each nightly backup is now also kept on the videos drive." if config.storage.drive_backups
        else "Backups are kept on the SD card only."))


@router.post("/backups/now")
def backup_now(request: Request,
               viewer: ViewerContext = Depends(require_parent),
               helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    _result, error = _try(helper, "backup_now")
    if error:
        return _drives_page(request, helper, config, error=error, status_code=400)
    return _drives_page(request, helper, config, success="Backup made.")


@router.post("/backups/restore")
def backup_restore(request: Request, where: str = Form(...), name: str = Form(...),
                   viewer: ViewerContext = Depends(require_parent),
                   helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _drive_job(request, helper, config, "backup_restore", where=where, name=name)


# --- diagnostics --------------------------------------------------------------

def _app_checks(config: Config) -> list[dict]:
    from app.services import updates
    installed = updates.ytdlp_version()
    latest = updates.latest_ytdlp_version()
    if not installed:
        status, detail, fix = "fail", "The downloader (yt-dlp) is not installed.", \
            "Reinstall Curatables, or send the diagnostics report to whoever helps you."
    elif latest and latest != installed:
        status, detail, fix = "warn", f"Version {installed}; {latest} is out.", \
            "YouTube changes often and old versions stop working. Press Update the downloader."
    elif latest:
        status, detail, fix = "ok", f"Version {installed}, the newest.", ""
    else:
        status, detail, fix = "ok", f"Version {installed} (could not check for a newer one).", ""
    return [{"id": "ytdlp", "title": "Video downloader", "status": status, "detail": detail, "fix": fix}]


@router.get("/diagnostics", response_class=HTMLResponse)
def diagnostics_page(request: Request,
                     viewer: ViewerContext = Depends(require_parent),
                     helper: SystemHelper = Depends(get_system_helper),
                     config: Config = Depends(get_config)):
    from app.services import updates
    if not helper.available:
        return unavailable_page(request)
    result, error = _try(helper, "diagnostics")
    checks = (result or {}).get("checks", []) + _app_checks(config)
    order = {"fail": 0, "warn": 1, "ok": 2}
    return request.app.state.templates.TemplateResponse(request, "parent/system_diagnostics.html", {
        "request": request, "checks": sorted(checks, key=lambda c: order.get(c["status"], 3)),
        "errors": (result or {}).get("errors", []), "error": error,
        "update_state": updates.update_state(config),
    })


@router.post("/diagnostics/update-downloader")
def update_downloader(request: Request,
                      viewer: ViewerContext = Depends(require_parent),
                      config: Config = Depends(get_config)):
    from app.services import updates
    try:
        updates.request_update(config, kind=updates.KIND_YTDLP)
    except OSError as e:
        return PlainTextResponse(f"Could not request the update: {e}", status_code=500)
    return _waiting(request, "Updating the downloader",
                    "This takes a minute or two, then Curatables restarts. The page comes back by itself.",
                    "/parent/system/diagnostics", delay_ms=20000)


@router.get("/diagnostics/report.txt")
def diagnostics_report(viewer: ViewerContext = Depends(require_parent),
                       helper: SystemHelper = Depends(get_system_helper)):
    import time
    result, error = _try(helper, "diagnostics_report")
    if error:
        return PlainTextResponse(f"The report could not be made: {error}", status_code=503)
    name = time.strftime("curatables-diagnostics-%Y%m%d-%H%M.txt")
    return PlainTextResponse(result["text"], headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
