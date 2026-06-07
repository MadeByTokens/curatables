from __future__ import annotations
"""Theme-aware template rendering for kid UI."""

import os

from fastapi import Request
from jinja2 import FileSystemLoader
from fastapi.templating import Jinja2Templates

from app.models import ViewerContext

_TEMPLATES_ROOT = os.path.join(os.path.dirname(__file__), "templates")

# Cache Jinja2Templates instances by theme name to avoid rebuilding
# the environment on every request.
_theme_cache: dict[str, Jinja2Templates] = {}


def _get_templates(theme: str) -> Jinja2Templates:
    """Get or create a cached Jinja2Templates for the given theme."""
    if theme in _theme_cache:
        return _theme_cache[theme]

    dirs = []
    if theme and theme != "base":
        theme_dir = os.path.join(_TEMPLATES_ROOT, "themes", theme)
        if os.path.isdir(theme_dir):
            dirs.append(theme_dir)
    dirs.append(os.path.join(_TEMPLATES_ROOT, "base"))

    templates = Jinja2Templates(directory=dirs[0])
    if len(dirs) > 1:
        templates.env.loader = FileSystemLoader(dirs)

    _theme_cache[theme] = templates
    return templates


def render_kid_template(request: Request, viewer: ViewerContext,
                        template_name: str, context: dict):
    """Render a kid template with theme-aware directory chain.

    Resolution order:
    1. themes/{viewer.theme}/ (if exists)
    2. base/ (always)
    """
    theme = viewer.theme if viewer else "base"
    templates = _get_templates(theme)
    context["request"] = request
    context["viewer"] = viewer
    # Kid Jinja2Templates instances are built per-theme and don't share
    # the main app's context_processors list, so we inject csrf_token
    # here instead. Falls back to empty string if the app state somehow
    # doesn't have a CSRF service (tests that bypass create_app).
    csrf_svc = getattr(request.app.state, "csrf", None)
    if csrf_svc is not None and hasattr(request, "session"):
        context.setdefault("csrf_token",
                           csrf_svc.mint_token(request.session))
    else:
        context.setdefault("csrf_token", "")
    return templates.TemplateResponse(request, template_name, context)
