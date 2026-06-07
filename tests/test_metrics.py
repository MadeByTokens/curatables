"""Tests for the /metrics Prometheus exposition.

Covers: 404 by default (opt-in via config), 200 + Prometheus
content-type when enabled, the http_requests counter ticks for
arbitrary requests, and the parent_logins counter records the
right outcome label on success/failure/setup.
"""

import pytest

from app.services.metrics import MetricsService


def _enable_metrics(app) -> None:
    """Flip the conftest-default disabled MetricsService into the
    enabled state in place. The PrometheusMiddleware captures a
    reference to this same instance at startup, so mutating the
    object propagates without re-wiring the middleware."""
    app.state.metrics.enable()


class TestMetricsRouteDefault:
    def test_returns_404_when_not_enabled(self, client):
        resp = client.get("/metrics")
        assert resp.status_code == 404


class TestMetricsRouteEnabled:
    def test_returns_prometheus_content_type(self, client, app):
        _enable_metrics(app)
        resp = client.get("/metrics")
        assert resp.status_code == 200
        # Prometheus exposition format. The exact `version=...` token
        # has changed across prometheus_client releases (0.0.4 in older
        # builds, 1.0.0 in 0.25+); just assert the marker is present.
        ct = resp.headers["content-type"]
        assert ct.startswith("text/plain")
        assert "version=" in ct

    def test_body_includes_uptime_gauge(self, client, app):
        _enable_metrics(app)
        resp = client.get("/metrics")
        assert b"curatables_uptime_seconds" in resp.content

    def test_http_request_counter_increments(self, client, app):
        _enable_metrics(app)
        # First scrape — counter should be present at zero or at the
        # initial value.
        client.get("/parent/login")
        client.get("/parent/login")
        client.get("/parent/login")
        resp = client.get("/metrics")
        body = resp.content.decode()
        # The body has lines like:
        # curatables_http_requests_total{method="GET",status_class="2xx"} 4.0
        # We don't pin the exact number because /metrics itself bumps
        # the GET counter; just assert it crossed our floor.
        line = next(
            (l for l in body.splitlines()
             if l.startswith("curatables_http_requests_total")
             and 'method="GET"' in l
             and 'status_class="2xx"' in l),
            None,
        )
        assert line is not None, body
        value = float(line.rsplit(" ", 1)[1])
        assert value >= 3.0


class TestParentLoginCounter:
    def test_failure_records_failure_outcome(self, client, app):
        _enable_metrics(app)
        client.post("/parent/login", data={"password": "wrong"})
        body = client.get("/metrics").content.decode()
        line = next(
            (l for l in body.splitlines()
             if l.startswith("curatables_parent_logins_total")
             and 'outcome="failure"' in l),
            None,
        )
        assert line is not None, body
        assert float(line.rsplit(" ", 1)[1]) >= 1.0

    def test_success_records_success_outcome(self, client, app):
        _enable_metrics(app)
        client.post("/parent/login", data={"password": "testpass"})
        body = client.get("/metrics").content.decode()
        line = next(
            (l for l in body.splitlines()
             if l.startswith("curatables_parent_logins_total")
             and 'outcome="success"' in l),
            None,
        )
        assert line is not None, body
        assert float(line.rsplit(" ", 1)[1]) >= 1.0

    def test_setup_records_setup_outcome(self, client, app):
        _enable_metrics(app)
        # Force first-run mode so /parent/setup is allowed.
        app.state.config.parent.password_hash = None
        app.state.config.save()
        client.post(
            "/parent/setup",
            data={"password": "abcd", "password2": "abcd"},
        )
        body = client.get("/metrics").content.decode()
        line = next(
            (l for l in body.splitlines()
             if l.startswith("curatables_parent_logins_total")
             and 'outcome="setup"' in l),
            None,
        )
        assert line is not None, body
        assert float(line.rsplit(" ", 1)[1]) >= 1.0


class TestMetricsServiceDisabled:
    def test_recorders_are_noops_when_disabled(self):
        svc = MetricsService(enabled=False)
        # Every recorder must be safe to call; render returns empty.
        svc.record_http("GET", 200, 0.01)
        svc.record_parent_login("success")
        svc.record_download("success")
        svc.record_kid_play()
        svc.record_eviction(3)
        svc.set_uptime(123.4)
        body, _ = svc.render()
        assert body == b""
        assert svc.registry is None


def _scrape_counter(client, name: str, label_filter: str | None = None) -> float:
    """Pull a single counter value out of /metrics. Returns 0.0 when
    the counter is present but has no observations matching the label
    filter (Prometheus omits zero-value labelled lines, so absence is
    the contract for "never incremented")."""
    body = client.get("/metrics").content.decode()
    for line in body.splitlines():
        if line.startswith(name):
            if label_filter is None or label_filter in line:
                try:
                    return float(line.rsplit(" ", 1)[1])
                except (ValueError, IndexError):
                    continue
    return 0.0


class TestKidPlayCounter:
    def test_play_event_ticks_kid_plays(self, client, app):
        """Posting an event=play to /api/log must bump the
        curatables_kid_plays_total counter via EventService."""
        _enable_metrics(app)
        # /api/log accepts unauthenticated POSTs (kid telemetry).
        for _ in range(4):
            client.post("/api/log", data={
                "event": "play",
                "video_id": "youtube_abcdef",
            })
        assert _scrape_counter(client, "curatables_kid_plays_total") >= 4.0

    def test_non_play_event_does_not_tick(self, client, app):
        """Other event types (complete, react, etc.) must not bump
        the kid_plays counter — that one is play-specific."""
        _enable_metrics(app)
        client.post("/api/log", data={
            "event": "complete",
            "video_id": "youtube_abcdef",
        })
        assert _scrape_counter(client, "curatables_kid_plays_total") == 0.0


class TestDownloadCounter:
    def test_record_download_outcomes(self, client, app):
        """Direct service-level test — ContentService's download
        thread is async, so exercising it through the route would be
        flaky. Instead call the recorder helper directly to pin the
        contract that each outcome label increments independently."""
        _enable_metrics(app)
        metrics = app.state.metrics
        metrics.record_download("success")
        metrics.record_download("success")
        metrics.record_download("failure")
        metrics.record_download("disk_full")

        body = client.get("/metrics").content.decode()
        assert _scrape_counter(client, "curatables_downloads_total",
                               'outcome="success"') >= 2.0
        assert _scrape_counter(client, "curatables_downloads_total",
                               'outcome="failure"') >= 1.0
        assert _scrape_counter(client, "curatables_downloads_total",
                               'outcome="disk_full"') >= 1.0


class TestEvictionCounter:
    def test_record_eviction_with_count(self, client, app):
        _enable_metrics(app)
        app.state.metrics.record_eviction(3)
        app.state.metrics.record_eviction(5)
        assert _scrape_counter(client,
                               "curatables_video_evictions_total") == 8.0

    def test_record_eviction_zero_or_negative_is_noop(self, client, app):
        """The cleanup loop calls record_eviction(report.evicted_count)
        even when the count is 0 (the loop already gates on
        `if report.evicted_count:`, but the recorder must be safe
        anyway). Same for negative values, which can't legitimately
        arise but should not crash."""
        _enable_metrics(app)
        app.state.metrics.record_eviction(0)
        app.state.metrics.record_eviction(-2)
        assert _scrape_counter(client,
                               "curatables_video_evictions_total") == 0.0
