from __future__ import annotations
"""CSRF enforcement middleware.

Checks every state-mutating (POST/PUT/PATCH/DELETE) request for a
valid CSRF token. Allow-lists a small set of paths that legitimately
cannot send a token: first-run setup (no session exists yet), the
/api/log beacon from the kid watch page (fire-and-forget telemetry,
low value target).

The kid UI targets iOS 9 Safari (no fetch, no modern JS), so XHR-sent
tokens go in form-data rather than an Authorization header.
"""

import logging

from starlette.requests import Request
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)


# Paths exempt from CSRF enforcement. Must be complete path matches
# (startswith) — keep the list short and audited.
CSRF_EXEMPT_PREFIXES: tuple[str, ...] = (
    "/parent/setup",   # first-run, no session yet
    "/parent/login",   # login creates the session; caller can't have
                       # a token bound to it before submitting
    "/profiles/select",  # same: establishes the kid session
    "/profiles/pin",     # same
    "/api/log",        # telemetry beacon from kid watch page
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

        if any(path.startswith(p) for p in CSRF_EXEMPT_PREFIXES):
            await self.app(scope, receive, send)
            return

        # The session is populated by SessionMiddleware, which wraps
        # this middleware. Build a Request so we can read it.
        request = Request(scope, receive=receive)
        session = request.session

        # Buffer the body so we can parse it for a csrf_token field AND
        # still pass it through to the downstream app. Only small form
        # posts come through here; large uploads go to allow-listed paths.
        body = await request.body()

        token = _extract_token(request, body)
        if not self.csrf.validate(session, token):
            logger.warning("CSRF token missing or invalid: %s %s",
                           method, path)
            resp = PlainTextResponse(
                "CSRF token missing or invalid.", status_code=403)
            await resp(scope, receive, send)
            return

        # Replay the buffered body downstream.
        async def _replay():
            return {"type": "http.request", "body": body,
                    "more_body": False}

        await self.app(scope, _replay, send)


def _extract_token(request: Request, body: bytes) -> str:
    """CSRF token may arrive as a form field (default HTML form POSTs)
    or an X-CSRF-Token header (XHR). Header wins if both are present."""
    header = request.headers.get("x-csrf-token")
    if header:
        return header

    content_type = request.headers.get("content-type", "").lower()
    if "application/x-www-form-urlencoded" in content_type:
        from urllib.parse import parse_qs
        try:
            decoded = body.decode("utf-8", errors="replace")
        except Exception:
            return ""
        parsed = parse_qs(decoded, keep_blank_values=True)
        vals = parsed.get("csrf_token") or []
        return vals[0] if vals else ""

    if "multipart/form-data" in content_type:
        # Parsing multipart just to extract one field is expensive;
        # simpler approach: scan for the boundary-delimited field. For
        # our tiny forms this is adequate. The multipart parser in
        # starlette consumes the body which we've already buffered.
        import re
        # The value sits on its own line, terminated by the CRLF before
        # the next boundary — capture up to that CRLF. Do NOT exclude
        # '-': itsdangerous URLSafeTimedSerializer tokens contain '-',
        # '_' and '.', and excluding '-' truncated the token, making
        # every kid multipart /upload fail CSRF with a 403.
        m = re.search(
            br'name="csrf_token"\r?\n\r?\n([^\r\n]+)',
            body,
        )
        if m:
            return m.group(1).decode("utf-8", errors="replace").strip()
        return ""

    return ""
