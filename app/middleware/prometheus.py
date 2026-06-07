"""Prometheus instrumentation middleware.

Wraps every HTTP request and feeds two counters into the
``MetricsService``: one labelled by status class, one observing
request duration. When metrics are disabled the middleware short-
circuits — the recorder helpers are no-ops, but skipping the call
entirely also avoids a couple of dict lookups per request.
"""

from __future__ import annotations

import time

from starlette.types import ASGIApp, Receive, Scope, Send


class PrometheusMiddleware:
    def __init__(self, app: ASGIApp, metrics):
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive,
                       send: Send) -> None:
        if scope["type"] != "http" or not self.metrics.enabled:
            await self.app(scope, receive, send)
            return

        start = time.monotonic()
        status_holder = {"code": 0}

        async def _send(message):
            if message.get("type") == "http.response.start":
                status_holder["code"] = int(message.get("status", 0))
            await send(message)

        try:
            await self.app(scope, receive, _send)
        finally:
            self.metrics.record_http(
                method=scope.get("method", "UNKNOWN"),
                status_code=status_holder["code"],
                duration_seconds=time.monotonic() - start,
            )
