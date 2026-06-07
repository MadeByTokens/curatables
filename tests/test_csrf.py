"""Tests for CSRF protection — token minting, validation, and the
middleware that enforces it on state-mutating requests.

These tests use a fresh app WITHOUT the conftest override that
disables validation, because here we want to exercise the real thing.
"""

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app.main import create_app
from app.services.auth import AuthService
from app.services.csrf import CSRFService
from app.middleware.csrf import _extract_token
from app.dependencies import get_db


def _multipart_body(token: str) -> bytes:
    """A minimal multipart/form-data body carrying a csrf_token field
    plus a file part, mirroring what the kid /upload XHR sends."""
    return (
        b"------B\r\n"
        b'Content-Disposition: form-data; name="csrf_token"\r\n\r\n'
        + token.encode() + b"\r\n"
        b"------B\r\n"
        b'Content-Disposition: form-data; name="file"; filename="v.mp4"\r\n'
        b"Content-Type: video/mp4\r\n\r\n"
        b"\x00\x00\x00\x00\r\n"
        b"------B--\r\n"
    )


def _multipart_request() -> Request:
    return Request({
        "type": "http",
        "method": "POST",
        "headers": [(b"content-type", b"multipart/form-data; boundary=----B")],
    })


@pytest.fixture
def csrf_app(tmp_path):
    """App with CSRF validation actually enabled (unlike the default
    conftest `app` fixture)."""
    import sqlite3
    from app.db.schema import init_schema
    app = create_app(data_dir=str(tmp_path))
    AuthService(app.state.config).set_password("testpass")

    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)

    def _override_db():
        yield conn
    app.dependency_overrides[get_db] = _override_db
    # Crucially: do NOT patch app.state.csrf.validate here.
    yield app
    conn.close()


@pytest.fixture
def csrf_client(csrf_app):
    return TestClient(csrf_app, follow_redirects=False)


class TestCSRFService:
    def test_valid_token_round_trips(self):
        svc = CSRFService("a" * 32)
        session = {}
        token = svc.mint_token(session)
        assert svc.validate(session, token) is True

    def test_token_bound_to_session_nonce(self):
        svc = CSRFService("a" * 32)
        session_a = {}
        session_b = {}
        token_a = svc.mint_token(session_a)
        svc.mint_token(session_b)  # different nonce
        # token_a does not validate against session_b
        assert svc.validate(session_b, token_a) is False

    def test_wrong_secret_rejects_token(self):
        svc1 = CSRFService("secret1")
        svc2 = CSRFService("secret2")
        session = {}
        token = svc1.mint_token(session)
        # svc2 sees the same session nonce but can't verify the signature
        assert svc2.validate(session, token) is False

    def test_empty_token_rejected(self):
        svc = CSRFService("s")
        session = {"_csrf_nonce": "n"}
        assert svc.validate(session, "") is False
        assert svc.validate(session, None) is False


class TestCSRFMiddleware:
    def test_post_without_token_is_rejected(self, csrf_client):
        # Establish a session by GET-ing the login page
        csrf_client.get("/parent/login")
        # Some state-mutating endpoint NOT on the exempt list — pick one
        # that doesn't require auth so we isolate the CSRF response.
        # /parent/content/bulk requires auth; /watch/*/comment does not
        # (handler checks viewer.profile_id internally).
        resp = csrf_client.post("/watch/nonexistent/comment",
                                data={"body": "test"})
        assert resp.status_code == 403
        assert b"CSRF token" in resp.content

    def test_exempt_path_bypasses_csrf(self, csrf_client):
        """Login should work without a token since the login IS the
        action that creates the session the token would bind to."""
        resp = csrf_client.post("/parent/login",
                                data={"password": "wrong"})
        # 200 (re-rendered login with error) or 302 — either way NOT 403
        assert resp.status_code != 403

    def test_get_requests_never_blocked(self, csrf_client):
        """Non-mutating methods bypass the CSRF check entirely."""
        resp = csrf_client.get("/parent/login")
        assert resp.status_code == 200


class TestMultipartTokenExtraction:
    """Regression: the kid /upload posts multipart/form-data, and CSRF
    tokens (itsdangerous URLSafeTimedSerializer) contain '-', '_' and
    '.'. The extractor's regex used to stop at the first '-', truncating
    the token so every kid upload failed CSRF with a 403."""

    def test_token_with_dashes_extracted_whole(self):
        token = "abc-def_ghi.jkl-MNO-pqr"
        got = _extract_token(_multipart_request(), _multipart_body(token))
        assert got == token

    def test_real_minted_token_round_trips_through_multipart(self):
        svc = CSRFService("a" * 32)
        session = {}
        token = svc.mint_token(session)          # real token: has - _ .
        extracted = _extract_token(_multipart_request(), _multipart_body(token))
        assert extracted == token
        assert svc.validate(session, extracted) is True
