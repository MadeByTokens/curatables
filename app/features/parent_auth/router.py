"""Parent authentication: setup, login, logout."""

import time

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import (
    get_auth_service, get_config, get_login_throttle, get_system_helper, get_viewer, require_parent,
)
from app.services.auth import AuthService, LoginThrottle, MIN_PASSWORD_LENGTH
from app.services.system_helper import (
    SAME_AS_LINUX, SAME_AS_PARENT, HelperFailed, HelperUnavailable, SystemHelper,
)
from app.config import Config
from app.models import ViewerContext

router = APIRouter(prefix="/parent", tags=["parent-auth"])


def _setup_closed(request: Request, config: Config) -> Response | None:
    """The 'setup has timed out' page once setup_window_minutes have passed
    since the server started without a password, else None."""
    window = config.parent.setup_window_minutes
    if window <= 0 or time.monotonic() - request.app.state.start_time <= window * 60:
        return None
    page: Response = request.app.state.templates.TemplateResponse(
        request, "parent/setup_closed.html",
        {"request": request, "minutes": window}, status_code=403)
    return page


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, config: Config = Depends(get_config)):
    if not config.is_first_run:
        return RedirectResponse(url="/parent/login", status_code=302)
    closed = _setup_closed(request, config)
    if closed:
        return closed
    return request.app.state.templates.TemplateResponse(request, "parent/setup.html", {
        "request": request, "error": None,
    })


@router.post("/setup")
def setup_submit(request: Request,
                 password: str = Form(...),
                 password2: str = Form(...),
                 auth: AuthService = Depends(get_auth_service),
                 config: Config = Depends(get_config),
                 helper: SystemHelper = Depends(get_system_helper)):
    if not config.is_first_run:
        return RedirectResponse(url="/parent/login", status_code=302)
    closed = _setup_closed(request, config)
    if closed:
        return closed

    templates = request.app.state.templates
    if len(password) < MIN_PASSWORD_LENGTH:
        return templates.TemplateResponse(request, "parent/setup.html", {
            "request": request,
            "error": f"Password must be at least {MIN_PASSWORD_LENGTH} characters.",
        })
    if password != password2:
        return templates.TemplateResponse(request, "parent/setup.html", {
            "request": request, "error": "Passwords do not match.",
        })

    if helper.linux_password_matches(password):
        return templates.TemplateResponse(request, "parent/setup.html", {
            "request": request, "error": SAME_AS_LINUX,
        })

    auth.set_password(password)
    auth.start_parent_session(request.session)
    request.app.state.metrics.record_parent_login("setup")
    if helper.available and not config.parent.system_setup_done:
        return RedirectResponse(url="/parent/setup/system", status_code=302)
    return RedirectResponse(url="/parent/", status_code=302)


def _needs_current(account: dict, config: Config) -> bool:
    """A Linux password set before (Imager, userconf.txt) can only be
    replaced by someone who knows it, unless the parent reset the box
    from its SD card."""
    return bool(account.get("has_password")) and not config.parent.linux_password_reset


def _system_page(request: Request, helper: SystemHelper, error: str | None = None):
    templates = request.app.state.templates
    config = request.app.state.config
    try:
        account = helper.call("account_status")
    except (HelperUnavailable, HelperFailed) as e:
        return templates.TemplateResponse(request, "parent/setup_system_unavailable.html", {
            "request": request, "error": str(e)}, status_code=503)
    return templates.TemplateResponse(request, "parent/setup_system.html", {
        "request": request, "account": account, "error": error,
        "needs_current": _needs_current(account, config),
    }, status_code=400 if error else 200)


@router.get("/setup/system", response_class=HTMLResponse)
def setup_system_page(request: Request,
                      viewer: ViewerContext = Depends(require_parent),
                      config: Config = Depends(get_config),
                      helper: SystemHelper = Depends(get_system_helper)):
    if config.parent.system_setup_done or not helper.available:
        return RedirectResponse(url="/parent/", status_code=302)
    return _system_page(request, helper)


@router.post("/setup/system")
def setup_system_submit(request: Request,
                        mode: str = Form("new"),
                        password: str = Form(""),
                        password2: str = Form(""),
                        current_password: str = Form(""),
                        ssh: str = Form(""),
                        viewer: ViewerContext = Depends(require_parent),
                        auth: AuthService = Depends(get_auth_service),
                        config: Config = Depends(get_config),
                        helper: SystemHelper = Depends(get_system_helper)):
    if config.parent.system_setup_done or not helper.available:
        return RedirectResponse(url="/parent/", status_code=302)
    try:
        account = helper.call("account_status")
    except (HelperUnavailable, HelperFailed) as e:
        if mode == "later":
            config.parent.system_setup_done = True
            config.save()
            return RedirectResponse(url="/parent/", status_code=302)
        return _system_page(request, helper, str(e))

    if auth.verify_password(password):
        return _system_page(request, helper, SAME_AS_PARENT)
    try:
        if mode == "keep":
            if not account.get("has_password"):
                return _system_page(request, helper, "No password was set before. Choose one above.")
            if not helper.linux_password_matches(password):
                return _system_page(request, helper, "That is not the current password.")
        else:
            if _needs_current(account, config) and not helper.linux_password_matches(current_password):
                return _system_page(request, helper, "The current Linux password is not right. "
                                    "Forgot it? See \"Forgot the parent password\" on the sign-in page: "
                                    "the SD-card reset lets you choose a new one.")
            if len(password) < MIN_PASSWORD_LENGTH:
                return _system_page(request, helper,
                                    f"The password must be at least {MIN_PASSWORD_LENGTH} characters.")
            if password != password2:
                return _system_page(request, helper, "The passwords do not match.")
            helper.call("account_set_password", password=password)
    except (HelperUnavailable, HelperFailed) as e:
        return _system_page(request, helper, str(e))

    config.parent.system_setup_done = True
    config.parent.linux_password_reset = False
    config.save()
    want_ssh = ssh == "1"
    if want_ssh != bool(account.get("ssh_enabled")):
        try:
            helper.call("ssh_set", enabled=want_ssh)
        except (HelperUnavailable, HelperFailed):
            return RedirectResponse(url="/parent/system?notice=ssh-failed#ssh", status_code=302)
    return RedirectResponse(url="/parent/", status_code=302)


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request,
               config: Config = Depends(get_config),
               viewer: ViewerContext = Depends(get_viewer)):
    if config.is_first_run:
        return RedirectResponse(url="/parent/setup", status_code=302)
    if viewer.is_parent:
        return RedirectResponse(url="/parent/", status_code=302)
    return request.app.state.templates.TemplateResponse(request, "parent/login.html", {
        "request": request, "error": None,
    })


@router.post("/login")
def login_submit(request: Request,
                 password: str = Form(...),
                 auth: AuthService = Depends(get_auth_service),
                 throttle: LoginThrottle = Depends(get_login_throttle)):
    client_key = request.client.host if request.client else "unknown"
    if throttle.blocked(client_key):
        return request.app.state.templates.TemplateResponse(request, "parent/login.html", {
            "request": request,
            "error": "Too many attempts. Wait a few minutes and try again.",
        }, status_code=429)
    if auth.verify_password(password):
        # get_viewer prefers profile_id over the parent flag, so
        # signing in also clears any selected kid profile.
        throttle.succeeded(client_key)
        auth.start_parent_session(request.session)
        request.app.state.metrics.record_parent_login("success")
        return RedirectResponse(url="/parent/", status_code=302)
    throttle.failed(client_key)
    request.app.state.metrics.record_parent_login("failure")
    return request.app.state.templates.TemplateResponse(request, "parent/login.html", {
        "request": request, "error": "Incorrect password.",
    })


@router.post("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/parent/login", status_code=302)


@router.get("/logout")
def logout_link(request: Request):
    return RedirectResponse(url="/parent/", status_code=302)
