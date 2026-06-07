"""Tests for the /healthz operational probe.

Covers: public access (no auth), JSON shape, version reporting,
first-run accessibility (probe must answer before setup), and the
DB-degraded path returning 503 with the structured error string.
"""

import sqlite3

import pytest

from app import __version__


class TestHealthz:
    def test_returns_200_with_ok_status(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"

    def test_reports_running_version(self, client):
        resp = client.get("/healthz")
        assert resp.json()["version"] == __version__

    def test_reports_uptime_seconds(self, client):
        resp = client.get("/healthz")
        uptime = resp.json()["uptime_seconds"]
        assert isinstance(uptime, (int, float))
        assert uptime >= 0.0

    def test_no_auth_required(self, client):
        """The unauthenticated `client` fixture has no parent session;
        /healthz must still answer 200."""
        resp = client.get("/healthz")
        assert resp.status_code == 200

    def test_works_during_first_run(self, client, app):
        """A fresh install (no password set) must still answer /healthz —
        otherwise the post-install smoke check has nothing to probe."""
        app.state.config.parent.password_hash = None
        app.state.config.save()
        resp = client.get("/healthz", follow_redirects=False)
        # Crucially: not a 302 to /parent/setup.
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_db_failure_reports_degraded(self, client, app, monkeypatch):
        """When the DB connection raises on SELECT 1, the route must
        return 503 + status=degraded with the error class in the body
        so external monitors can route on the HTTP code and operators
        can read the diagnosis."""
        from app.dependencies import get_db

        def _broken_db():
            class _Conn:
                def execute(self, *a, **kw):
                    raise sqlite3.OperationalError("simulated db outage")

                def close(self):
                    pass
            yield _Conn()

        app.dependency_overrides[get_db] = _broken_db
        try:
            resp = client.get("/healthz")
        finally:
            # Restore the test's real DB override so subsequent tests
            # in the same session don't see a poisoned connection.
            app.dependency_overrides.pop(get_db, None)

        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["db"].startswith("error: OperationalError")
        # Non-DB fields still present so operators can read the version
        # and uptime alongside the failure.
        assert body["version"] == __version__
        assert body["uptime_seconds"] is not None
