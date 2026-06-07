from __future__ import annotations
"""Per-request correlation ID — stored in a contextvar so log records
anywhere in the request lifecycle can emit it alongside their message.

Respects a pre-existing X-Request-ID header (set by a reverse proxy)
so the ID propagates across hops; otherwise mints a fresh short UUID.
"""

import contextvars
import uuid

from starlette.types import ASGIApp, Receive, Scope, Send


# The contextvar is shared across the app. `log_request_id_filter` below
# reads it when formatting every log record.
request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def new_request_id() -> str:
    """12 hex chars — short enough to eyeball, random enough to be unique
    across realistic request volumes for a household server."""
    return uuid.uuid4().hex[:12]


def current_request_id() -> str:
    """Current request's ID, or '-' if called outside a request scope."""
    return request_id_var.get()


class RequestIDMiddleware:
    """ASGI middleware that stamps each request with a correlation ID."""

    def __init__(self, app: ASGIApp,
                 header_name: str = "x-request-id"):
        self.app = app
        # lower-case for case-insensitive header match; ASGI headers are
        # always bytes + lowercase per the spec.
        self.header_bytes = header_name.encode("latin-1").lower()

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Look for an incoming header (reverse proxy may have stamped one).
        incoming = None
        for name, value in scope.get("headers", []):
            if name == self.header_bytes:
                try:
                    incoming = value.decode("latin-1").strip()
                except UnicodeDecodeError:
                    incoming = None
                break
        rid = incoming or new_request_id()

        token = request_id_var.set(rid)
        try:
            async def send_with_header(message):
                if message["type"] == "http.response.start":
                    headers = list(message.get("headers", []))
                    headers.append((self.header_bytes, rid.encode("latin-1")))
                    message["headers"] = headers
                await send(message)

            await self.app(scope, receive, send_with_header)
        finally:
            request_id_var.reset(token)


class RequestIDLogFilter:
    """Logging filter that injects `request_id` into every LogRecord so
    a `%(request_id)s` token in the formatter resolves even for log
    calls made outside the request scope (where it's '-')."""

    def filter(self, record):
        record.request_id = current_request_id()
        return True
