from __future__ import annotations
"""Reject requests with oversized bodies before Starlette tries to parse
them.

Without this, a `curl -d @500MB_file /watch/X/comment` would force the
full body into memory to run the service-layer 500-char check. On a
16 GB mini-PC, a handful of such requests is an OOM. The middleware
drops them at the Content-Length check — no body is read.

Each route class gets its own ceiling:
- parent video uploads (tus, under /parent/upload): `upload_limit`;
- the kid's single multipart upload (POST /upload): `kid_upload_limit`
  plus a little multipart overhead;
- picture uploads (kid/parent edit forms, the import page) and
  curation-list imports, and the import page's details form: `image_limit`;
- everything else: 1 MB.
Limits are callables so a change in Settings applies without a restart.

This middleware must be the outermost one: anything that reads the body
before it (CSRF reads form bodies) would buffer an oversized request
into memory first.
"""

from typing import Callable, Union

from starlette.types import ASGIApp, Receive, Scope, Send


_SMALL_LIMIT = 1_000_000  # 1 MB default for all non-upload endpoints
_IMAGE_LIMIT = 6 * 1024 * 1024
_MULTIPART_SLACK = 1_000_000

Limit = Union[int, Callable[[], int]]


def _value(limit: Limit) -> int:
    return limit() if callable(limit) else limit


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, upload_limit: Limit,
                 kid_upload_limit: Limit | None = None,
                 image_limit: Limit = _IMAGE_LIMIT,
                 default_limit: int = _SMALL_LIMIT):
        self.app = app
        self.upload_limit = upload_limit
        self.kid_upload_limit = kid_upload_limit if kid_upload_limit is not None else upload_limit
        self.image_limit = image_limit
        self.default_limit = default_limit

    def _limit_for(self, path: str) -> int:
        if path.startswith("/parent/upload"):
            return _value(self.upload_limit)
        if path == "/upload":
            return _value(self.kid_upload_limit) + _MULTIPART_SLACK
        if path.endswith("/edit") and path.startswith(("/video/", "/channel/", "/parent/content/")):
            return _value(self.image_limit)
        if path.startswith("/parent/channels/import"):
            return _value(self.image_limit)
        if path.startswith("/parent/import/") and (
                path.endswith("/picture") or path.startswith("/parent/import/batch/")):
            return _value(self.image_limit)
        return self.default_limit

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Check the Content-Length header up front. This catches the
        # common case where a malicious client declares the size
        # honestly (or a broken client sends a too-large body).
        limit = self._limit_for(scope["path"])
        for name, value in scope.get("headers", []):
            if name == b"content-length":
                try:
                    length = int(value)
                except ValueError:
                    await self._send_413(send)
                    return
                if length > limit:
                    await self._send_413(send)
                    return
                break

        # Chunked transfer / missing Content-Length: wrap receive to
        # count bytes and abort if the running total exceeds the limit.
        total = 0
        limit_ref = limit

        async def _wrapped_receive():
            nonlocal total
            message = await receive()
            if message.get("type") == "http.request":
                body = message.get("body", b"")
                total += len(body)
                if total > limit_ref:
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, _wrapped_receive, send)

    @staticmethod
    async def _send_413(send: Send) -> None:
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        })
        await send({
            "type": "http.response.body",
            "body": b"Request body too large.",
        })
