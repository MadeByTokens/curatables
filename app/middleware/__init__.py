"""HTTP middleware for curatables server."""

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from app.middleware.body_size import BodySizeLimitMiddleware
from app.middleware.csrf import CSRFMiddleware
from app.middleware.prometheus import PrometheusMiddleware
from app.middleware.request_id import (
    RequestIDMiddleware, RequestIDLogFilter, current_request_id,
)

access_logger = logging.getLogger("curatables.access")


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Log every HTTP request with method, path, status, and duration."""

    async def dispatch(self, request: Request, call_next):
        start = time.monotonic()
        response = await call_next(request)
        duration_ms = (time.monotonic() - start) * 1000

        path = request.url.path
        # Static files are noisy — log at DEBUG
        level = logging.DEBUG if path.startswith("/static/") else logging.INFO
        access_logger.log(
            level, "%s %s %d %.0fms",
            request.method, path, response.status_code, duration_ms,
        )
        return response


__all__ = [
    "RequestLoggingMiddleware",
    "BodySizeLimitMiddleware",
    "CSRFMiddleware",
    "PrometheusMiddleware",
    "RequestIDMiddleware",
    "RequestIDLogFilter",
    "current_request_id",
]
