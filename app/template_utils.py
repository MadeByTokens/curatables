"""Template rendering for the kid UI."""
from __future__ import annotations

import hashlib
import os
from datetime import date, datetime

from fastapi import Request
from fastapi.templating import Jinja2Templates

from jinja2 import pass_context

from app.constants import THEME_CHOICES
from app.i18n import (
    LANGUAGES, context_language, gettext, jinja_gettext, jinja_language, js_messages,
)
from app.models import ViewerContext

_TEMPLATES_ROOT = os.path.join(os.path.dirname(__file__), "templates", "base")

_templates: Jinja2Templates | None = None


def friendly_date(value: str | None, today: date | None = None, lang: str = "en") -> str:
    """'today', 'yesterday', 'N days ago' or the date, from a
    'YYYY-MM-DD[ HH:MM:SS]' string. Pre-readers and early readers cope
    far better with these than with raw timestamps."""
    if not value:
        return ""
    try:
        day = datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return str(value)
    delta = ((today or date.today()) - day).days
    if delta <= 0:
        return gettext(lang, "today")
    if delta == 1:
        return gettext(lang, "yesterday")
    if delta < 7:
        return gettext(lang, "{n} days ago", n=delta)
    return day.strftime("%d %b %Y").lstrip("0")


def _asset_version() -> str:
    """Short fingerprint of the static files, appended to asset URLs so
    browsers fetch new CSS/JS after an upgrade instead of a cached copy."""
    static_root = os.path.join(os.path.dirname(__file__), "static")
    digest = hashlib.sha1()
    for dirpath, _dirs, files in sorted(os.walk(static_root)):
        for name in sorted(files):
            st = os.stat(os.path.join(dirpath, name))
            digest.update(f"{name}:{st.st_size}:{int(st.st_mtime)}".encode())
    return digest.hexdigest()[:10]


_DOWNLOAD_ERROR_ADVICE = {
    "bot_blocked": "The site blocked the download. Press Retry after updating "
                   "yt-dlp (Settings → Updates), or turn on Browser Impersonation "
                   "(Settings → Advanced → Anti-Bot).",
    "unavailable": "The video is private, removed, or not available where you live.",
    "age_restricted": "The site wants a logged-in account for this video "
                      "(Settings → Advanced → Anti-Bot → cookies).",
    "network": "Couldn't reach the site. Check the internet connection, then press Retry.",
    "format": "No downloadable copy was found, or it is bigger than the download "
              "size limit (Settings → Advanced).",
    "disk_full": "The disk is full. Free some space (Storage), then press Retry.",
}


def friendly_download_error(raw: str | None) -> str:
    """Plain advice for a stored '[type] message' download error."""
    if not raw:
        return ""
    if raw.startswith("[") and "]" in raw:
        kind = raw[1:raw.index("]")]
        if kind in _DOWNLOAD_ERROR_ADVICE:
            return _DOWNLOAD_ERROR_ADVICE[kind]
    return "The download failed. Press Retry; if it keeps failing, update yt-dlp in Settings."


@pass_context
def _friendly_date_filter(ctx, value: str | None) -> str:
    return friendly_date(value, lang=context_language(ctx))


def register_filters(templates: Jinja2Templates) -> None:
    templates.env.filters["friendly_date"] = _friendly_date_filter
    templates.env.globals["_"] = jinja_gettext
    templates.env.globals["page_language"] = jinja_language
    templates.env.globals["js_messages"] = js_messages
    templates.env.globals["kid_languages"] = LANGUAGES
    templates.env.filters["friendly_download_error"] = friendly_download_error
    templates.env.globals["asset_v"] = _asset_version()
    templates.env.globals["kid_themes"] = [key for key, _label in THEME_CHOICES if key != "base"]


def _get_templates() -> Jinja2Templates:
    """Fallback environment for code paths without the app's templates
    (unit tests that render outside create_app)."""
    global _templates
    if _templates is None:
        _templates = Jinja2Templates(directory=_TEMPLATES_ROOT)
        register_filters(_templates)
    return _templates


def render_kid_template(request: Request, viewer: ViewerContext,
                        template_name: str, context: dict):
    """Render a kid template. Themes are pure CSS: kid/_shell.html links
    the theme stylesheet for the viewer's profile."""
    templates = getattr(request.app.state, "templates", None) or _get_templates()
    context["request"] = request
    context["viewer"] = viewer
    # The kid Jinja2Templates instance doesn't share the main app's
    # context_processors list, so csrf_token is injected here. Falls back to empty string if the app state somehow
    # doesn't have a CSRF service (tests that bypass create_app).
    csrf_svc = getattr(request.app.state, "csrf", None)
    if csrf_svc is not None and hasattr(request, "session"):
        context.setdefault("csrf_token",
                           csrf_svc.mint_token(request.session))
    else:
        context.setdefault("csrf_token", "")
    return templates.TemplateResponse(request, template_name, context)
