"""CSRF enforcement middleware.

Every POST/PUT/PATCH/DELETE must carry a valid CSRF token, either in an
X-CSRF-Token header (the pages' XHR code) or as a `csrf_token` form
field. A short, audited list of paths that cannot have a token yet is
exempt; those only refuse requests another site's page sent.
"""
from __future__ import annotations

import logging
import re
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


# The first-run password page has no session yet. Step 2
# (/parent/setup/system) runs inside a parent session and is checked.
CSRF_EXEMPT_EXACT: frozenset[str] = frozenset({"/parent/setup"})

CSRF_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/parent/login",   # login creates the session; caller can't have
                       # a token bound to it before submitting
    "/profiles/select",  # same: establishes the kid session
    "/profiles/pin",     # same
    "/api/log",        # telemetry beacon from kid watch page
    "/api/clock",      # a device's time while the box's clock is behind;
                       # sent from pages that may have no session yet
    # Parent tus uploads — the vendored tus-js-client doesn't know
    # about CSRF tokens. Parent auth + SameSite=strict cookie covers
    # the threat model on this LAN-only, parent-only endpoint.
    "/parent/upload",
)

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


class CSRFMiddleware:
    def __init__(self, app: ASGIApp, csrf_service):
        self.app = app
        self.csrf = csrf_service

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"].upper()
        path = scope["path"]

        if method not in _MUTATING_METHODS:
            await self.app(scope, receive, send)
            return

        if path in CSRF_EXEMPT_EXACT or any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES):
            # No token here, so at least refuse a form another site posted
            # (signing the parent out, claiming first-run setup).
            if _cross_site(scope):
                await self._refuse(scope, receive, send, method, path)
                return
            await self.app(scope, receive, send)
            return

        # The session is populated by SessionMiddleware, which wraps
        # this middleware. Build a Request so we can read it.
        request = Request(scope, receive=receive)
        session = request.session

        if request.headers.get("x-csrf-token"):
            if not self.csrf.validate(session, request.headers["x-csrf-token"]):
                await self._refuse(scope, receive, send, method, path)
                return
            await self.app(scope, receive, send)
            return

        # Read only as much of the body as finding the token needs: all of
        # a small form, but just the start of a multipart upload (the
        # token is the form's first field). A request with a bad token is
        # refused before a large upload is ever held in memory.
        multipart = "multipart/form-data" in request.headers.get("content-type", "").lower()
        prefix = b""
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            prefix += message.get("body", b"")
            more = message.get("more_body", False)
            if multipart and (len(prefix) >= _MULTIPART_PREFIX or (
                    _TOKEN_FIELD in prefix
                    and b"\n" in prefix.split(_TOKEN_FIELD, 1)[1].lstrip(b"\r\n"))):
                break

        token = _extract_token(request, prefix)
        if not self.csrf.validate(session, token):
            await self._refuse(scope, receive, send, method, path)
            return

        sent_prefix = False

        async def _replay():
            nonlocal sent_prefix
            if not sent_prefix:
                sent_prefix = True
                return {"type": "http.request", "body": prefix, "more_body": more}
            return await receive()

        await self.app(scope, _replay, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send, method: str, path: str) -> None:
        logger.warning("CSRF token missing or invalid: %s %s", method, path)
        resp = PlainTextResponse("CSRF token missing or invalid.", status_code=403)
        await resp(scope, receive, send)


def _cross_site(scope: Scope) -> bool:
    """True when the browser says the request came from a page on another
    site (Origin header naming a different host). Requests without the
    header (old browsers, tools) are let through."""
    headers = dict(scope.get("headers") or [])
    origin = headers.get(b"origin", b"").decode("latin-1").strip()
    if not origin:
        return False
    if origin == "null":
        return True
    host = headers.get(b"host", b"").decode("latin-1").strip().lower()
    return bool(urlsplit(origin).netloc.lower() != host)


_MULTIPART_PREFIX = 64 * 1024
_TOKEN_FIELD = b'name="csrf_token"'


# The field's value is the whole line after the part headers. Tokens
# contain '-', '_' and '.', so nothing but the line end may stop it.
_MULTIPART_TOKEN_RE = re.compile(br'name="csrf_token"\r?\n\r?\n([^\r\n]+)')


def _extract_token(request: Request, body: bytes) -> str:
    """The csrf_token form field from a (start of a) form body."""
    content_type = request.headers.get("content-type", "").lower()
    if "application/x-www-form-urlencoded" in content_type:
        parsed = parse_qs(body.decode("utf-8", errors="replace"), keep_blank_values=True)
        vals = parsed.get("csrf_token") or []
        return vals[0] if vals else ""

    if "multipart/form-data" in content_type:
        # A scan instead of a multipart parse: only one field is needed.
        m = _MULTIPART_TOKEN_RE.search(body)
        if m:
            return m.group(1).decode("utf-8", errors="replace").strip()
        return ""

    return ""
