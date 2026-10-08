"""Tests for BodySizeLimitMiddleware — early rejection of oversized
request bodies before they hit the service layer."""

import pytest


class TestBodySizeLimit:
    def test_small_body_passes_through(self, client):
        """A normal-sized POST to a non-upload path should not be blocked."""
        # Wrong password, but that's fine — we only care that the
        # middleware let it through to the login handler (which returns
        # 200 with an error flash, not 413).
        resp = client.post("/parent/login", data={"password": "wrong"})
        assert resp.status_code != 413

    def test_oversized_body_rejected_on_non_upload_path(self, client):
        """A POST to /parent/login with a 2MB body declared via
        Content-Length should get 413 before parsing."""
        big = b"x" * 2_000_000
        resp = client.post(
            "/parent/login",
            content=big,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert resp.status_code == 413

    def test_oversized_body_rejected_on_comment_endpoint(self, client):
        """The DoS vector the middleware is meant to close: a huge
        POST to /watch/<id>/comment before the service-level 500-char
        check fires."""
        big = b"body=" + (b"x" * 2_000_000)
        resp = client.post(
            "/watch/fakevid/comment",
            content=big,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert resp.status_code == 413

    def test_upload_endpoint_allows_larger_body(self, client):
        """Upload paths get the bigger ceiling. 2MB must not be
        rejected with 413 on /upload (it may be rejected for auth
        or other reasons, which is unrelated)."""
        big = b"x" * 2_000_000
        resp = client.post(
            "/upload",
            content=big,
            headers={"Content-Type": "application/octet-stream"},
        )
        assert resp.status_code != 413

    def test_malformed_content_length_is_rejected(self, client):
        """A non-integer Content-Length header is treated as invalid
        → 413."""
        resp = client.post(
            "/parent/login",
            content=b"x",
            headers={"Content-Length": "not-a-number"},
        )
        # httpx/starlette may normalize Content-Length before we see
        # it. Accept either 413 (middleware caught it) or 400
        # (framework rejected earlier) — both are correct, both stop
        # the request.
        assert resp.status_code in (400, 413)


class TestRouteLimits:
    def _mw(self, **kw):
        from app.middleware.body_size import BodySizeLimitMiddleware
        return BodySizeLimitMiddleware(app=None, upload_limit=10_000_000_000,
                                       kid_upload_limit=lambda: 500, **kw)

    def test_picture_routes_get_image_limit(self):
        mw = self._mw()
        assert mw._limit_for("/video/abc/edit") == 6 * 1024 * 1024
        assert mw._limit_for("/channel/3/edit") == 6 * 1024 * 1024

    def test_kid_upload_uses_kid_limit(self):
        assert self._mw()._limit_for("/upload") == 500 + 1_000_000

    def test_parent_tus_uses_upload_limit(self):
        assert self._mw()._limit_for("/parent/upload/tus/x") == 10_000_000_000

    def test_kid_upload_over_limit_rejected_before_csrf(self, client, app):
        app.state.config.storage.max_kid_upload_bytes = 10
        resp = client.post(
            "/upload", content=b"x" * 2_000_000,
            headers={"Content-Type": "application/octet-stream"})
        assert resp.status_code == 413
