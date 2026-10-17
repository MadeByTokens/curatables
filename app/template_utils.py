"""Template rendering for the kid UI."""
from __future__ import annotations

import hashlib
import os
from datetime import date, datetime, timezone
from typing import Callable
from zoneinfo import available_timezones

from fastapi import Request
from fastapi.templating import Jinja2Templates
from jinja2 import pass_context

from app.constants import THEME_CHOICES
from app.i18n import (
    LANGUAGES, context_language, gettext, jinja_gettext, jinja_language, js_messages,
)
from app.models import ViewerContext
from app.services.family_time import family_today, family_zone

_TEMPLATES_ROOT = os.path.join(os.path.dirname(__file__), "templates", "base")

_templates: Jinja2Templates | None = None


def friendly_date(value: str | None, today: date | None = None, lang: str = "en",
                  tz: str = "") -> str:
    """'today', 'yesterday', 'N days ago' or the date, from a
    'YYYY-MM-DD[ HH:MM:SS]' string. Pre-readers and early readers cope
    far better with these than with raw timestamps. A value with a time
    is stored UTC and is counted in the family's zone `tz`."""
    if not value:
        return ""
    text = str(value)
    try:
        if len(text) >= 19:
            stamp = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            day = stamp.astimezone(family_zone(tz)).date()
        else:
            day = datetime.strptime(text[:10], "%Y-%m-%d").date()
    except ValueError:
        return text
    delta = ((today or family_today(tz)) - day).days
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
    "rate_limited": "The site asked the box to slow down for a while. "
                    "Wait an hour or so, then press Retry.",
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


def excerpt(text: str | None, limit: int) -> str:
    """text cut to at most limit characters: at the end of a sentence when
    one ends in the second half, else at a word with "…" added."""
    if not text or len(text) <= limit:
        return text or ""
    cut = text[:limit]
    sentence_end = max(cut.rfind(mark) for mark in (". ", "! ", "? ", "\n"))
    if sentence_end >= limit // 2:
        return cut[:sentence_end + 1].rstrip()
    space = cut.rfind(" ")
    return (cut[:space] if space > 0 else cut).rstrip() + "…"


def local_time(value: str | None, today: date | None = None, lang: str = "en",
               tz: str = "") -> str:
    """A stored UTC timestamp ('YYYY-MM-DD HH:MM:SS' or ISO 8601) as the
    family's day and time: 'today 18:36', '1 Sep 2026 09:10'."""
    if not value:
        return ""
    try:
        stamp = datetime.strptime(str(value)[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return str(value)
    local = stamp.replace(tzinfo=timezone.utc).astimezone(family_zone(tz))
    return f"{friendly_date(local.strftime('%Y-%m-%d'), today, lang, tz)} {local:%H:%M}"


def register_filters(templates: Jinja2Templates,
                     timezone_name: Callable[[], str] = lambda: "") -> None:
    @pass_context
    def _friendly_date_filter(ctx, value: str | None) -> str:
        return friendly_date(value, lang=context_language(ctx), tz=timezone_name())

    @pass_context
    def _local_time_filter(ctx, value: str | None) -> str:
        return local_time(value, lang=context_language(ctx), tz=timezone_name())

    templates.env.filters["friendly_date"] = _friendly_date_filter
    templates.env.filters["local_time"] = _local_time_filter
    templates.env.globals["_"] = jinja_gettext
    templates.env.globals["page_language"] = jinja_language
    templates.env.globals["js_messages"] = js_messages
    templates.env.globals["kid_languages"] = LANGUAGES
    templates.env.globals["timezones"] = lambda: sorted(available_timezones())
    templates.env.filters["friendly_download_error"] = friendly_download_error
    templates.env.filters["excerpt"] = excerpt
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
    # The fallback environment has no csrf context processor.
    csrf_svc = getattr(request.app.state, "csrf", None)
    if csrf_svc is not None and hasattr(request, "session"):
        context.setdefault("csrf_token",
                           csrf_svc.mint_token(request.session))
    else:
        context.setdefault("csrf_token", "")
    return templates.TemplateResponse(request, template_name, context)
