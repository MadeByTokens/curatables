from __future__ import annotations
"""Reject requests with oversized bodies before Starlette tries to parse
them.

Without this, a `curl -d @500MB_file /watch/X/comment` would force the
full body into memory to run the service-layer 500-char check. On a
16 GB mini-PC, a handful of such requests is an OOM. The middleware
drops them at the Content-Length check — no body is read.

Legitimate upload endpoints have a separate, larger ceiling drawn from
`config.storage.max_upload_bytes` so parent video uploads (gigabytes)
still work. Everything else is capped at 1 MB — comments, forms, POST
bodies for the kid/parent UIs never legitimately exceed that.
"""

from starlette.types import ASGIApp, Receive, Scope, Send


_SMALL_LIMIT = 1_000_000  # 1 MB default for all non-upload endpoints


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, upload_limit: int,
                 upload_path_prefixes: tuple[str, ...] = (
                     "/upload",
                     "/parent/upload",
                     "/tus",
                     "/parent/content/",   # custom thumbnail upload (/edit)
                     "/video/",            # kid thumbnail upload (/edit)
                     "/channel/",          # kid banner/icon upload (/edit)
                 ),
                 default_limit: int = _SMALL_LIMIT):
        self.app = app
        self.upload_limit = upload_limit
        self.upload_path_prefixes = upload_path_prefixes
        self.default_limit = default_limit

    def _limit_for(self, path: str) -> int:
        if any(path.startswith(p) for p in self.upload_path_prefixes):
            return self.upload_limit
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
