"""Shared test fixtures — in-memory SQLite, config, app, test client."""

import pytest
import sqlite3
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Config
from app.db.schema import init_schema
from app.main import create_app
from app.dependencies import get_db
from app.services.auth import AuthService


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
    for subdir in ["db", "videos", "thumbnails/custom", "logs"]:
        (tmp_path / subdir).mkdir(parents=True, exist_ok=True)
    return c


def _make_test_db():
    """Create an in-memory SQLite connection with schema for testing.
    check_same_thread=False needed because TestClient runs routes in a worker thread."""
    conn = sqlite3.connect(":memory:", check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_schema(conn)
    return conn


@pytest.fixture
def app(tmp_path):
    """Full FastAPI app with in-memory DB for route testing.
    Password is pre-set so first-run redirect doesn't fire.
    CSRF validation is disabled so existing tests can POST without
    having to fetch + attach a token first. Route-level auth is still
    enforced as in production."""
    app = create_app(data_dir=str(tmp_path))

    # Set a password so the app isn't in first-run mode
    auth = AuthService(app.state.config)
    auth.set_password("testpass")

    # CSRF is exercised in dedicated tests (tests/test_csrf.py) where
    # we validate real tokens end-to-end. Everywhere else we short-
    # circuit validation so the existing test corpus doesn't have to
    # thread tokens through every POST.
    app.state.csrf.validate = lambda session, token: True

    # Override get_db to use a shared in-memory connection
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
