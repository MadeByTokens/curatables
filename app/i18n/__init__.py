"""Translations for the kid screens.

Each catalog (`<code>.json` next to this file) maps the English text, as
written in the templates and kid scripts, to the translated text.
Missing entries fall back to English. `{name}` placeholders are filled
by `gettext(lang, text, name=...)`.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from jinja2 import pass_context

LANGUAGES = (
    ("en", "English"),
    ("pt-BR", "Português (Brasil)"),
    ("es", "Español"),
)
LANGUAGE_CODES = tuple(code for code, _name in LANGUAGES)
DEFAULT_LANGUAGE = "en"

JS_MESSAGES = (
    "Pick a video first.",
    "That video is too big ({size}). Ask a grown-up.",
    "Sending...",
    "Sending: {pct}%",
    "Getting it ready...",
    "This video is too big. Ask a grown-up to help.",
    "That didn’t work. Try again.",
    "Too many in a row. Wait a minute, then try again.",
    "That didn’t work. Ask a grown-up to reload the page.",
    "Stopped. Pick another video, or tap Send it again.",
)
"""Strings the kid scripts show; sent to the page translated (see
kid/_shell.html and static/kid/i18n.js)."""

SERVER_MESSAGES = (
    "Too many tries. Wait a little, then try again.",
    "Wrong PIN. Try again.",
    "This video isn't here.",
    "There's nothing here.",
    "That channel isn't available. Pick one from the list.",
    "This video is too big. Ask a grown-up to help.",
    "There isn't enough free space on this computer. Ask a grown-up.",
    "This video is in a format we can't read. Ask a grown-up for help.",
    "We couldn't read this video file. Try a different one.",
    "This video is already here. Ask a grown-up to find it.",
    "Upload failed. Try again.",
    "Channel name is required.",
    "Channel name must be 64 characters or fewer.",
    "That name is already used. Try another one.",
    "That picture didn't work. Try another one.",
    "Let's look for something else.",
    "Whoa, so many taps! Let's slow down a little, then try again.",
    "Lots of searching right now. Try again in a minute.",
    "Searching is taking a nap. Try again later.",
    "Searching isn't working right now. Try again later.",
    "That channel can't be shown. Try another one.",
    "That didn’t work. Try again.",
    "A grown-up said not this one.",
    "You can't ask for this one. Try another video.",
    "Parent",
    "today",
    "yesterday",
    "{n} days ago",
)
"""Kid-facing text produced in Python (route errors, dates); translated
where it is rendered."""

_DIR = Path(__file__).parent


@lru_cache(maxsize=None)
def catalog(lang: str) -> dict[str, str]:
    if lang == DEFAULT_LANGUAGE or lang not in LANGUAGE_CODES:
        return {}
    data: dict[str, str] = json.loads((_DIR / f"{lang}.json").read_text(encoding="utf-8"))
    return data


def normalize(lang: str | None) -> str:
    return lang if lang in LANGUAGE_CODES else DEFAULT_LANGUAGE


def gettext(lang: str | None, text: str, **values: Any) -> str:
    out = catalog(normalize(lang)).get(text) or text
    return out.format(**values) if values else out


def js_messages(lang: str | None) -> dict[str, str]:
    return {text: gettext(lang, text) for text in JS_MESSAGES}


def context_language(ctx: Any) -> str:
    """The language a kid template renders in: an explicit `lang`, else
    the kid's profile language, else the server's default kid language."""
    lang = ctx.get("lang")
    if lang:
        return normalize(lang)
    viewer = ctx.get("viewer")
    if viewer is not None and getattr(viewer, "is_child", False):
        return normalize(getattr(viewer, "language", None))
    request = ctx.get("request")
    config = getattr(getattr(getattr(request, "app", None), "state", None), "config", None)
    if config is not None:
        return normalize(config.server.kid_language)
    return DEFAULT_LANGUAGE


@pass_context
def jinja_gettext(ctx: Any, text: str, **values: Any) -> str:
    return gettext(context_language(ctx), text, **values)


@pass_context
def jinja_language(ctx: Any) -> str:
    return context_language(ctx)
