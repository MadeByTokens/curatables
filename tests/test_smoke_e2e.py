"""End-to-end smoke test — boots a real uvicorn subprocess and walks
the parent-setup → kid-grid golden path, with yt-dlp mocked at the
boundary so the suite is hermetic (no network, no flakiness).

This test is *not* in the default fast-unit run; pyproject.toml's
``addopts = -m 'not smoke'`` deselects it. Run it explicitly with::

    pytest -m smoke

The subprocess imports ``yt_dlp`` from
``tests/fixtures/fake_ytdlp/`` instead of the real package — that
directory is prepended to ``PYTHONPATH`` by the ``server`` fixture
below. The fake returns deterministic metadata for any URL and
writes a 4-byte placeholder file on download, which is enough surface
for the add → preview → confirm → ready path to run end-to-end.

Why a real server, given we already have 400+ in-process tests?
TestClient skips the actual ASGI lifespan unless wrapped in a
context manager, never binds a real socket, and gives no signal on
template-cache / DI-wiring / migrator failures that only appear at
process boot. Booting via ``run.py`` exercises that whole path.
"""

import json
import os
import re
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
FAKE_YTDLP_PATH = REPO_ROOT / "tests" / "fixtures" / "fake_ytdlp"


def _free_port() -> int:
    """Bind to port 0 so the kernel hands us a free port. Inevitable
    TOCTOU race against another local listener — acceptable for an
    isolated test box."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _write_pre_seeded_config(data_dir: Path, port: int) -> None:
    """Pre-seed config.json so mDNS is off (multicast is flaky in CI
    sandboxes) and the cache cleanup loop is disabled (so teardown
    isn't racing a background task)."""
    cfg = {
        "server": {
            "port": port,
            "host": "127.0.0.1",
            "log_level": "warning",
            "mdns_enabled": False,
            "mdns_name": "Curatables",
        },
        "storage": {
            "path": str(data_dir),
            "cache_cleanup_interval_minutes": 0,
        },
        "parent": {
            "password_hash": None,
        },
    }
    (data_dir / "config.json").write_text(json.dumps(cfg))


def _wait_for_server(base_url: str, timeout: float = 15.0) -> None:
    """Poll /healthz until it answers 200 or we give up.

    /healthz is the canonical readiness probe: it short-circuits the
    first-run redirect, hits the database, and returns a structured
    JSON body. A 200 here means the migrator ran, the DB is open, and
    the request stack is wired."""
    deadline = time.monotonic() + timeout
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{base_url}/healthz",
                          follow_redirects=False, timeout=2.0)
            if r.status_code == 200:
                return
        except httpx.HTTPError as e:
            last_exc = e
        time.sleep(0.2)
    raise AssertionError(
        f"Server at {base_url} did not respond within {timeout}s "
        f"(last error: {last_exc!r})"
    )


@pytest.fixture
def server(tmp_path):
    port = _free_port()
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    _write_pre_seeded_config(data_dir, port)

    # Prepend the fake yt-dlp dir to PYTHONPATH so `import yt_dlp`
    # in the subprocess resolves to our stub instead of the installed
    # package. PYTHONPATH-listed dirs land on sys.path before the
    # site-packages entries that ship the real yt-dlp.
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        str(FAKE_YTDLP_PATH) + (os.pathsep + existing_pp if existing_pp else "")
    )

    proc = subprocess.Popen(
        [
            sys.executable, "run.py",
            "--data-dir", str(data_dir),
            "--port", str(port),
            "--host", "127.0.0.1",
            "--log-level", "warning",
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )

    base_url = f"http://127.0.0.1:{port}"
    try:
        _wait_for_server(base_url)
    except AssertionError:
        proc.terminate()
        try:
            _, err = proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            err = b""
        raise AssertionError(
            f"server at {base_url} failed to come up; stderr:\n"
            f"{err.decode(errors='replace')}"
        )

    try:
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def _csrf_from_html(html: bytes) -> str:
    m = re.search(rb'name="csrf_token"\s+value="([^"]+)"', html)
    return m.group(1).decode() if m else ""


@pytest.mark.smoke
class TestSmokeBoot:
    def test_healthz_reports_ok(self, server):
        """The fixture only returned the server URL once /healthz was
        green, so this assertion is fast — but it also pins the JSON
        contract so a future refactor can't silently change the
        operational shape."""
        r = httpx.get(f"{server}/healthz")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["db"] == "ok"
        assert body["version"]
        assert body["uptime_seconds"] is not None

    def test_setup_page_is_served(self, server):
        """Server is up, the migrator ran, templates load, and the
        first-run setup page renders."""
        r = httpx.get(f"{server}/parent/setup", follow_redirects=False)
        assert r.status_code == 200
        assert b"password" in r.content.lower()

    def test_full_golden_path(self, server):
        client = httpx.Client(base_url=server, follow_redirects=False,
                              timeout=10.0)

        # 1. Setup completes and the response authenticates the parent.
        r = client.post(
            "/parent/setup",
            data={"password": "smoketest", "password2": "smoketest"},
        )
        assert r.status_code == 302, r.content
        assert r.headers["location"] == "/parent/"

        # 2. The parent dashboard renders for the new session.
        r = client.get("/parent/")
        assert r.status_code == 200

        # 3. /parent/content (the canonical content page) renders for
        # an empty library. 200 (page) or 3xx (slash-canonicalisation
        # / redirect to a default) both prove the route is wired.
        r = client.get("/parent/content/", follow_redirects=True)
        assert r.status_code == 200

        # 4. Parent profiles list page renders.
        r = client.get("/parent/profiles/")
        assert r.status_code == 200

        # 5. Open the create-profile form so we can mint a CSRF token.
        r = client.get("/parent/profiles/create")
        assert r.status_code == 200
        token = _csrf_from_html(r.content)
        assert token, "expected csrf_token hidden input on the create form"

        # 6. Create a kid profile with no PIN so the picker can select
        # it without a PIN gate.
        r = client.post("/parent/profiles/create", data={
            "display_name": "SmokeKid",
            "pin": "",
            "avatar": "default",
            "theme": "base",
            "search_mode": "disabled",
            "csrf_token": token,
        })
        assert r.status_code == 302, (
            f"profile create failed: {r.status_code} body={r.content!r}"
        )

        # 7. The kid-side profile picker renders or auto-selects the
        # new profile. With one PIN-less kid it auto-selects and
        # redirects to / (the kid grid). Follow through so the
        # assertion lands on whichever page the picker chose.
        r = client.get("/profiles", follow_redirects=True)
        assert r.status_code == 200
        assert b"SmokeKid" in r.content


@pytest.mark.smoke
class TestSmokeMetadataFetch:
    """Walk the /parent/add → preview path with yt-dlp mocked at
    the boundary by the fixtures/fake_ytdlp/ stub. The fake returns
    a deterministic single video for plain URLs and an entries list
    for channel/playlist-shaped URLs, which is enough to cover both
    branches of ContentService.fetch_preview."""

    def _setup(self, client) -> None:
        r = client.post(
            "/parent/setup",
            data={"password": "smoketest", "password2": "smoketest"},
        )
        assert r.status_code == 302, r.content

    def test_add_page_is_reachable(self, server):
        client = httpx.Client(base_url=server, follow_redirects=False,
                              timeout=10.0)
        self._setup(client)
        r = client.get("/parent/add")
        assert r.status_code == 200

    def _csrf_token_from_add_page(self, client) -> str:
        """Open /parent/add and pull the CSRF token off the form so
        the subsequent POST clears the CSRFMiddleware check."""
        r = client.get("/parent/add")
        assert r.status_code == 200
        token = _csrf_from_html(r.content)
        assert token, "expected csrf_token hidden input on /parent/add"
        return token

    def test_video_url_renders_preview_with_fake_metadata(self, server):
        """Submit a single-video URL and assert the preview page
        shows the fake yt-dlp's deterministic title and channel.
        This is the load-bearing test: a regression that broke
        metadata fetch (template loading, DI wiring on the
        backend, info-dict projection) shows up here."""
        client = httpx.Client(base_url=server, follow_redirects=False,
                              timeout=15.0)
        self._setup(client)
        token = self._csrf_token_from_add_page(client)

        r = client.post("/parent/add", data={
            "url": "https://example.com/smoke-video",
            "csrf_token": token,
        })
        assert r.status_code == 200, (
            f"expected preview render; got {r.status_code} body={r.content[:300]!r}"
        )
        # The fake's deterministic strings should land on the preview.
        assert b"Smoke Test Video" in r.content
        assert b"Smoke Test Channel" in r.content

    def test_channel_url_renders_preview_with_entries(self, server):
        """A YouTube channel URL routes through parse_url's channel
        branch -> backend.fetch_channel_videos -> the fake returns an
        entries list -> the preview should list every item.

        ``parse_url`` is YouTube-aware (its regex only matches
        ``youtube.com/c/...`` etc.), so the test URL must be on
        youtube.com to land in the channel branch. Non-YouTube URLs
        always fall through to the catch-all single-video path,
        which is the case the previous test exercises."""
        client = httpx.Client(base_url=server, follow_redirects=False,
                              timeout=15.0)
        self._setup(client)
        token = self._csrf_token_from_add_page(client)

        r = client.post("/parent/add", data={
            "url": "https://www.youtube.com/c/smoke-channel",
            "csrf_token": token,
        })
        assert r.status_code == 200
        # Three fake items by design; assert at least the first and
        # last show up so we know the entries projection isn't dropping
        # rows.
        assert b"Smoke Item 1" in r.content
        assert b"Smoke Item 3" in r.content
