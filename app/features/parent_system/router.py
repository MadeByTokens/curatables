"""The Box pages: the Linux login, SSH, Wi-Fi, restarting, and the
progress page for the system helper's background tasks."""

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from app.dependencies import get_auth_service, get_system_helper, require_parent
from app.models import ViewerContext
from app.services.auth import MIN_PASSWORD_LENGTH, AuthService
from app.services.system_helper import (
    SAME_AS_PARENT, HelperFailed, HelperUnavailable, SystemHelper,
)

router = APIRouter(prefix="/parent/system", tags=["parent-system"])

SAFE_BACK = ("/parent/system",)


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
