"""The Box pages: the Linux login, SSH, Wi-Fi, restarting, and the
progress page for the system helper's background tasks."""

import shutil
import time
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

from app.config import Config
from app.dependencies import get_auth_service, get_config, get_system_helper, require_parent
from app.models import ViewerContext
from app.services import updates
from app.services.auth import MIN_PASSWORD_LENGTH, AuthService
from app.services.family_time import family_zone
from app.services.system_helper import (
    SAME_AS_PARENT, HelperFailed, HelperUnavailable, SystemHelper,
)

router = APIRouter(prefix="/parent/system", tags=["parent-system"])

SAFE_BACK = ("/parent/system", "/parent/import")

BACK_LABELS = {
    "/parent/system": "Back to Login, network & power",
    "/parent/system/drives": "Back to Drives & backups",
    "/parent/system/updates": "Back to Updates",
    "/parent/system/diagnostics": "Back to Diagnostics",
    "/parent/import": "Go to Import",
}

# Fixed messages a page can show after returning with ?notice=<key>.
BOX_NOTICES = {
    "restarted": "Curatables restarted.",
    "rebooted": "The box restarted.",
    "network": "The network reconnected.",
    "task-gone": "That task ended while the box's helper restarted (this is normal at the end of an "
                 "update). Its result is shown on this page.",
}


def back_label(back: str) -> str:
    return BACK_LABELS.get(back.split("?", 1)[0].split("#", 1)[0], "Back")


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
    if request.query_params.get("notice") == "ssh-failed":
        return _overview(request, helper, error=(
            "Setup is done, but SSH could not be switched as you asked. Try again below."))
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


def _safe_back(back: str) -> str:
    return back if back.startswith(SAFE_BACK) and "//" not in back else "/parent/system"


def _with_notice(back: str, notice: str) -> str:
    path, _, anchor = back.partition("#")
    joined = f"{path}{'&' if '?' in path else '?'}notice={notice}"
    return f"{joined}#{anchor}" if anchor else joined


def _waiting(request: Request, title: str, message: str, back: str, *, notice: str,
             expect_down: bool, delay_ms: int = 4000) -> Response:
    """A page that waits for the box to answer again, then returns to back.
    With expect_down, it first waits for the server to go away, so it
    cannot return before a restart has even begun."""
    back = _safe_back(back)
    page: Response = request.app.state.templates.TemplateResponse(request, "parent/system_wait.html", {
        "request": request, "title": title, "message": message, "back": back,
        "back_label": back_label(back), "return_to": _with_notice(back, notice),
        "expect_down": expect_down, "delay_ms": delay_ms})
    return page


@router.post("/restart")
def restart(request: Request, back: str = Form("/parent/system"),
            viewer: ViewerContext = Depends(require_parent),
            helper: SystemHelper = Depends(get_system_helper)):
    _result, error = _try(helper, "restart_curatables")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _waiting(request, "Restarting Curatables",
                    "This takes about half a minute. The page comes back by itself.", back,
                    notice="restarted", expect_down=True)


@router.post("/reboot")
def reboot(request: Request, back: str = Form("/parent/system"),
           viewer: ViewerContext = Depends(require_parent),
           helper: SystemHelper = Depends(get_system_helper)):
    _result, error = _try(helper, "reboot")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _waiting(request, "Restarting the box",
                    "This takes one to two minutes. The page comes back by itself.", back,
                    notice="rebooted", expect_down=True)


@router.post("/shutdown")
def shutdown(request: Request,
             viewer: ViewerContext = Depends(require_parent),
             helper: SystemHelper = Depends(get_system_helper)):
    _result, error = _try(helper, "shutdown")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    page: Response = request.app.state.templates.TemplateResponse(
        request, "parent/system_shutdown.html", {"request": request})
    return page


@router.post("/network-restart")
def network_restart(request: Request, back: str = Form("/parent/system"),
                    viewer: ViewerContext = Depends(require_parent),
                    helper: SystemHelper = Depends(get_system_helper)):
    _result, error = _try(helper, "restart_network")
    if error:
        return _overview(request, helper, error=error, status_code=400)
    return _waiting(request, "Reconnecting to the network",
                    "The connection drops for a few seconds. The page comes back by itself.", back,
                    notice="network", expect_down=False, delay_ms=8000)


@router.get("/jobs/{job_id}.json")
def job_json(job_id: str,
             viewer: ViewerContext = Depends(require_parent),
             helper: SystemHelper = Depends(get_system_helper)):
    job, error = _try(helper, "job_status", id=job_id)
    if error and "not known" in error:
        return JSONResponse({"state": "gone", "error": error})
    if error:
        return JSONResponse({"state": "unknown", "error": error}, status_code=503)
    return JSONResponse(job)


@router.post("/jobs/{job_id}/cancel")
def job_cancel(request: Request, job_id: str, back: str = Form("/parent/system"),
               viewer: ViewerContext = Depends(require_parent),
               helper: SystemHelper = Depends(get_system_helper)):
    """Ask a running task to stop; only a step that can be undone (a
    copy) takes it, and the task then cleans up after itself."""
    _try(helper, "job_cancel", id=job_id)
    return RedirectResponse(url=f"/parent/system/jobs/{quote(job_id)}?back={quote(_safe_back(back))}",
                            status_code=303)


@router.get("/jobs/{job_id}", response_class=HTMLResponse)
def job_page(request: Request, job_id: str, back: str = "/parent/system",
             viewer: ViewerContext = Depends(require_parent),
             helper: SystemHelper = Depends(get_system_helper)):
    back = _safe_back(back)
    job, error = _try(helper, "job_status", id=job_id)
    if error and "not known" in error:
        return RedirectResponse(url=_with_notice(back, "task-gone"), status_code=303)
    return request.app.state.templates.TemplateResponse(request, "parent/system_job.html", {
        "request": request, "job": job, "error": error, "job_id": job_id, "back": back,
        "back_label": back_label(back),
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
    if not helper.available:
        return unavailable_page(request)
    result, error = _try(helper, "diagnostics")
    checks = (result or {}).get("checks", []) + _app_checks(config)
    for c in checks:
        # The helper reports the clock in UTC (right for the report file);
        # the page says it the way the family reads every other time.
        if c.get("id") == "clock" and c.get("status") == "ok":
            now = datetime.now(timezone.utc).astimezone(family_zone(config.server.timezone))
            c["detail"] = f"{now:%Y-%m-%d %H:%M} ({now.tzname()}). Set from the internet."
    order = {"fail": 0, "warn": 1, "ok": 2}
    return request.app.state.templates.TemplateResponse(request, "parent/system_diagnostics.html", {
        "request": request, "checks": sorted(checks, key=lambda c: order.get(c["status"], 3)),
        "errors": (result or {}).get("errors", []), "error": error,
        "update_state": updates.update_state(config),
    })


@router.post("/diagnostics/update-downloader")
def update_downloader(request: Request, back: str = Form("/parent/system/updates"),
                      viewer: ViewerContext = Depends(require_parent),
                      helper: SystemHelper = Depends(get_system_helper),
                      config: Config = Depends(get_config)):
    if updates.pending_request(config) is None:
        try:
            updates.request_update(config, kind=updates.KIND_YTDLP)
        except OSError as e:
            return _updates_page(request, helper, config, status_code=500,
                                 error=f"The downloader update could not be started: {e}")
    # The page shows each step and the result; the update carries on
    # on the box if the parent leaves it.
    return RedirectResponse(url=_safe_back(back) + "#downloader", status_code=303)


@router.get("/updates/downloader.json")
def downloader_state(viewer: ViewerContext = Depends(require_parent), config: Config = Depends(get_config)):
    state = updates.update_state(config)
    return JSONResponse({"running": state["pending"] is not None, "progress": state["progress"]},
                        headers={"Cache-Control": "no-store"})


@router.get("/diagnostics/report.txt")
def diagnostics_report(viewer: ViewerContext = Depends(require_parent),
                       helper: SystemHelper = Depends(get_system_helper)):
    result, error = _try(helper, "diagnostics_report")
    if error:
        return PlainTextResponse(f"The report could not be made: {error}", status_code=503)
    name = time.strftime("curatables-diagnostics-%Y%m%d-%H%M.txt")
    return PlainTextResponse(result["text"], headers={
        "Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


# --- updates ------------------------------------------------------------------

UPDATES = "/parent/system/updates"


def _updates_page(request: Request, helper: SystemHelper, config: Config, *,
                  error: str | None = None, status_code: int = 200) -> Response:
    if not helper.available:
        return unavailable_page(request)
    info, load_error = _try(helper, "update_info")
    page: Response = request.app.state.templates.TemplateResponse(request, "parent/system_updates.html", {
        "request": request, "info": info or {}, "error": error or load_error,
        "update_state": updates.update_state(config),
    }, status_code=status_code)
    return page


@router.get("/updates", response_class=HTMLResponse)
def updates_page(request: Request,
                 viewer: ViewerContext = Depends(require_parent),
                 helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _updates_page(request, helper, config)


def _update_job(request: Request, helper: SystemHelper, config: Config, action: str) -> Response:
    job, error = _try(helper, action)
    if error:
        return _updates_page(request, helper, config, error=error, status_code=400)
    return job_redirect(job, UPDATES)


@router.post("/updates/curatables")
def update_curatables(request: Request,
                      viewer: ViewerContext = Depends(require_parent),
                      helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _update_job(request, helper, config, "curatables_update")


@router.post("/updates/rollback")
def rollback_curatables(request: Request,
                        viewer: ViewerContext = Depends(require_parent),
                        helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _update_job(request, helper, config, "curatables_rollback")


@router.post("/updates/system")
def update_system(request: Request,
                  viewer: ViewerContext = Depends(require_parent),
                  helper: SystemHelper = Depends(get_system_helper), config: Config = Depends(get_config)):
    return _update_job(request, helper, config, "system_update")
