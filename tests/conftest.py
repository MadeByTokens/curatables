"""Shared test fixtures — in-memory SQLite, config, app, test client."""

import pytest
import sqlite3

from fastapi.testclient import TestClient

from app.config import Config
from app.db.schema import init_schema
from app.main import create_app
from app.dependencies import get_db
from app.services.auth import AuthService


@pytest.fixture(autouse=True)
def _test_client_host(monkeypatch):
    """TestClient talks to http://testserver."""
    monkeypatch.setenv("CURATABLES_ALLOWED_HOSTS", "testserver")


@pytest.fixture(autouse=True)
def _reset_rate_limiters():
    """The limiters are module-level and keyed on ids that repeat across
    fresh test DBs, so counts would otherwise leak between tests."""
    from app.services.auth import PICTURE_PIN_LIMITER, PIN_LIMITER
    from app.services.comments import _KID_RATE_LIMITER, _PARENT_RATE_LIMITER
    PIN_LIMITER.reset()
    PICTURE_PIN_LIMITER.reset()
    _KID_RATE_LIMITER.reset()
    _PARENT_RATE_LIMITER.reset()
    yield


@pytest.fixture
def db():
    """In-memory SQLite connection with full schema applied."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    yield conn
    conn.close()


@pytest.fixture
def config(tmp_path):
    """Config pointing at a temporary data directory."""
    c = Config()
    c.storage.path = str(tmp_path)
    for subdir in ["db", "videos", "thumbnails", "logs"]:
        (tmp_path / subdir).mkdir(parents=True, exist_ok=True)
    return c


def _make_test_db():
    """In-memory SQLite connection with schema applied.

    check_same_thread=False because TestClient runs routes in a worker
    thread."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


@pytest.fixture
def app(tmp_path):
    """Full FastAPI app with an in-memory DB for route testing.

    A parent password is pre-set so the first-run redirect doesn't fire.
    CSRF validation is short-circuited so tests can POST without fetching
    a token; tests/test_csrf.py exercises real tokens. Route-level auth is
    enforced as in production."""
    app = create_app(data_dir=str(tmp_path))

    auth = AuthService(app.state.config)
    auth.set_password("testpass")

    app.state.csrf.validate = lambda session, token: True

    conn = _make_test_db()

    def _override_db():
        yield conn

    app.dependency_overrides[get_db] = _override_db
    yield app
    conn.close()


@pytest.fixture
def client(app):
    """TestClient without authentication."""
    return TestClient(app, follow_redirects=False)


@pytest.fixture
def authed_client(app):
    """TestClient with parent session authenticated."""
    client = TestClient(app, follow_redirects=False)
    resp = client.post("/parent/login", data={"password": "testpass"})
    assert resp.status_code == 302
    return client
