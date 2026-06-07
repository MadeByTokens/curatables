"""Prometheus metrics collection (opt-in).

Curatables ships ``prometheus_client`` as a hard dependency but
keeps the metrics surface gated behind ``config.server.prometheus_enabled``.
Vanilla self-hosters don't get a counter surface; operators who
wire Curatables into Prometheus flip the flag and the /metrics
route starts answering.

The service is *always* instantiated so call-site code can call
``app.state.metrics.record_*`` unconditionally. When disabled, every
recorder is a no-op and the registry is ``None``.
"""

from __future__ import annotations

import logging
from typing import Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


logger = logging.getLogger(__name__)


# Histogram bucket boundaries in seconds. Tuned for a self-hosted LAN
# server where a 5-second response is already pathological — the long
# tail past 10s is a single +Inf bucket so we don't spend label
# cardinality on requests that should never happen.
_HTTP_DURATION_BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsService:
    """Owns the Prometheus collector registry + counters.

    The same instance lives on ``app.state.metrics`` for the whole
    process; callers (middleware, route handlers) capture a reference
    once at startup. ``enable()`` mutates the instance in place so
    tests and (some day) a runtime toggle can flip the surface on
    without rewiring middleware bindings.
    """

    def __init__(self, enabled: bool):
        self.enabled: bool = False
        self.registry: Optional[CollectorRegistry] = None
        if enabled:
            self.enable()

    def enable(self) -> None:
        """Build the registry and counters. Idempotent."""
        if self.enabled:
            return
        self.enabled = True
        self.registry = CollectorRegistry()

        self.http_requests = Counter(
            "curatables_http_requests_total",
            "HTTP requests served, labelled by method and status class.",
            ["method", "status_class"],
            registry=self.registry,
        )
        self.http_request_duration = Histogram(
            "curatables_http_request_duration_seconds",
            "HTTP request duration in seconds, labelled by method.",
            ["method"],
            buckets=_HTTP_DURATION_BUCKETS,
            registry=self.registry,
        )
        self.parent_logins = Counter(
            "curatables_parent_logins_total",
            "Parent login attempts, labelled by outcome (success / failure / setup).",
            ["outcome"],
            registry=self.registry,
        )
        # Reserved for call-site wiring. The counters exist so consumers
        # of the service can always call ``metrics.downloads.labels(...).inc()``
        # without first checking that the registry is built.
        self.downloads = Counter(
            "curatables_downloads_total",
            "Video download attempts and outcomes.",
            ["outcome"],
            registry=self.registry,
        )
        self.kid_plays = Counter(
            "curatables_kid_plays_total",
            "Kid-side video plays (event=play in /api/log).",
            registry=self.registry,
        )
        self.evictions = Counter(
            "curatables_video_evictions_total",
            "Cache-mode videos evicted by the background sweep.",
            registry=self.registry,
        )
        self.uptime = Gauge(
            "curatables_uptime_seconds",
            "Process uptime in seconds (set on each /metrics scrape).",
            registry=self.registry,
        )

    # ------------------------------------------------------------------
    # Recording helpers — every method is a no-op when ``enabled`` is
    # False so call sites don't need conditional branches.
    # ------------------------------------------------------------------

    def record_http(self, method: str, status_code: int,
                    duration_seconds: float) -> None:
        if not self.enabled:
            return
        status_class = f"{status_code // 100}xx" if status_code else "0xx"
        self.http_requests.labels(method=method, status_class=status_class).inc()
        self.http_request_duration.labels(method=method).observe(duration_seconds)

    def record_parent_login(self, outcome: str) -> None:
        """outcome: "success", "failure", or "setup" (first-run create)."""
        if not self.enabled:
            return
        self.parent_logins.labels(outcome=outcome).inc()

    def record_download(self, outcome: str) -> None:
        """outcome: "success", "failure", or "disk_full"."""
        if not self.enabled:
            return
        self.downloads.labels(outcome=outcome).inc()

    def record_kid_play(self) -> None:
        if not self.enabled:
            return
        self.kid_plays.inc()

    def record_eviction(self, count: int = 1) -> None:
        if not self.enabled or count <= 0:
            return
        self.evictions.inc(count)

    def set_uptime(self, seconds: float) -> None:
        if not self.enabled:
            return
        self.uptime.set(seconds)

    def render(self) -> tuple[bytes, str]:
        """Return ``(body, content_type)`` for a /metrics response.

        Caller is responsible for the 404-when-disabled case; this
        method only handles the enabled path.
        """
        if not self.enabled or self.registry is None:
            return b"", "text/plain; charset=utf-8"
        return generate_latest(self.registry), CONTENT_TYPE_LATEST
