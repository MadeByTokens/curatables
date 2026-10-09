"""Parent authentication: setup, login, logout."""

import time

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from app.dependencies import get_auth_service, get_config, get_login_throttle, get_viewer
from app.services.auth import AuthService, LoginThrottle, MIN_PASSWORD_LENGTH
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
                 config: Config = Depends(get_config)):
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

    auth.set_password(password)
    auth.start_parent_session(request.session)
    request.app.state.metrics.record_parent_login("setup")
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
