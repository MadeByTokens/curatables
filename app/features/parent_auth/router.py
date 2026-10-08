"""Parent authentication: setup, login, logout."""

from fastapi import APIRouter, Depends, Form, Request
from starlette.responses import HTMLResponse, RedirectResponse

from app.dependencies import get_auth_service, get_config, get_viewer
from app.services.auth import AuthService, LOGIN_LIMITER, MIN_PASSWORD_LENGTH
from app.config import Config
from app.models import ViewerContext

router = APIRouter(prefix="/parent", tags=["parent-auth"])


@router.get("/setup", response_class=HTMLResponse)
def setup_page(request: Request, config: Config = Depends(get_config)):
    if not config.is_first_run:
        return RedirectResponse(url="/parent/login", status_code=302)
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
                 auth: AuthService = Depends(get_auth_service)):
    client_key = request.client.host if request.client else "unknown"
    if not LOGIN_LIMITER.check(client_key):
        return request.app.state.templates.TemplateResponse(request, "parent/login.html", {
            "request": request,
            "error": "Too many attempts. Wait a few minutes and try again.",
        }, status_code=429)
    if auth.verify_password(password):
        # get_viewer prefers profile_id over the parent flag, so
        # signing in also clears any selected kid profile.
        auth.start_parent_session(request.session)
        request.app.state.metrics.record_parent_login("success")
        return RedirectResponse(url="/parent/", status_code=302)
    request.app.state.metrics.record_parent_login("failure")
    return request.app.state.templates.TemplateResponse(request, "parent/login.html", {
        "request": request, "error": "Incorrect password.",
    })


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/parent/login", status_code=302)
