"""Prometheus metrics, opt-in via ``config.server.prometheus_enabled``.

The service is always instantiated so call sites can call
``record_*`` unconditionally; when disabled every recorder is a no-op
and the registry is ``None``.
"""

from __future__ import annotations

from typing import Optional

from prometheus_client import (
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)


# Seconds. On a LAN server anything past a few seconds is pathological,
# so everything above 10 s shares the +Inf bucket.
_HTTP_DURATION_BUCKETS = (0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)


class MetricsService:
    """Owns the Prometheus collector registry and counters.

    One instance lives on ``app.state.metrics`` for the whole process and
    middleware captures it at startup, so ``enable()`` mutates it in place
    rather than replacing it.
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

    # Recorders: all no-ops while disabled.

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
