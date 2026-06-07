"""Tests for RequestIDMiddleware — stamps a correlation ID on every
request and exposes it to log records."""


class TestRequestIDHeader:
    def test_generated_when_missing(self, client):
        resp = client.get("/parent/login")
        rid = resp.headers.get("x-request-id")
        assert rid is not None
        assert len(rid) == 12  # short UUID4 hex slice

    def test_honors_incoming_header(self, client):
        resp = client.get("/parent/login",
                          headers={"X-Request-ID": "abc123deadbeef"})
        assert resp.headers.get("x-request-id") == "abc123deadbeef"

    def test_different_requests_get_different_ids(self, client):
        r1 = client.get("/parent/login")
        r2 = client.get("/parent/login")
        assert r1.headers["x-request-id"] != r2.headers["x-request-id"]

    def test_log_format_includes_request_id(self, caplog, client):
        """The log format is '%(request_id)s' — a record from a request
        handler should have a non-'-' request_id attribute."""
        import logging
        caplog.set_level(logging.INFO, logger="curatables.access")
        resp = client.get("/parent/login",
                          headers={"X-Request-ID": "testcorrid12"})
        assert resp.status_code == 200
        # The access logger fires on every request and now carries the RID.
        access_records = [r for r in caplog.records
                          if r.name == "curatables.access"]
        assert access_records
        assert any(getattr(r, "request_id", None) == "testcorrid12"
                   for r in access_records)
