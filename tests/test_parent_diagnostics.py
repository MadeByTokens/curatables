"""Box, Diagnostics: health checks, fixes, and the report download."""

import json
import pytest

from app.services import updates
from tests.fake_helper import FakeHelper


@pytest.fixture
def helper(app, monkeypatch):
    fake = FakeHelper(
        diagnostics={"checks": [
            {"id": "network", "title": "Network connection", "status": "ok", "detail": "Ethernet.", "fix": ""},
            {"id": "drive", "title": "Videos drive", "status": "fail", "detail": "Not plugged in.",
             "fix": "Plug the videos drive back in."},
        ], "errors": ["2026-10-09 boom"]},
        diagnostics_report={"text": "Curatables diagnostics report\n[hidden]\n"},
    )
    app.state.system_helper = fake
    app.state.config.parent.system_setup_done = True
    monkeypatch.setattr(updates, "ytdlp_version", lambda: "2026.09.01")
    monkeypatch.setattr(updates, "latest_ytdlp_version", lambda ttl=3600: "2026.10.01")
    return fake


def test_checks_are_shown_problems_first_with_fixes(authed_client, helper):
    page = authed_client.get("/parent/system/diagnostics").text
    assert page.index("Videos drive") < page.index("Network connection")
    assert "Plug the videos drive back in." in page
    assert "2026-10-09 boom" in page


def test_the_clock_is_shown_in_the_family_zone(app, authed_client, helper):
    helper.responses["diagnostics"]["checks"].append(
        {"id": "clock", "title": "Clock", "status": "ok", "detail": "2026-10-17 12:58 UTC", "fix": ""})
    app.state.config.server.timezone = "America/New_York"
    page = authed_client.get("/parent/system/diagnostics").text
    assert "UTC" not in page
    assert "(EDT)" in page or "(EST)" in page


def test_an_old_downloader_is_flagged(authed_client, helper):
    page = authed_client.get("/parent/system/diagnostics").text
    assert "2026.10.01 is out" in page and "Update the downloader" in page


def test_report_downloads_as_a_text_file(authed_client, helper):
    resp = authed_client.get("/parent/system/diagnostics/report.txt")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"].startswith('attachment; filename="curatables-diagnostics-')
    assert "Curatables diagnostics report" in resp.text


def test_report_needs_a_parent(client, helper):
    assert client.get("/parent/system/diagnostics/report.txt").status_code in (302, 303)


def test_update_button_requests_an_update(authed_client, app, helper, monkeypatch):
    asked = []
    monkeypatch.setattr(updates, "request_update", lambda config, kind: asked.append(kind))
    resp = authed_client.post("/parent/system/diagnostics/update-downloader", follow_redirects=False)
    assert asked == [updates.KIND_YTDLP]
    assert resp.status_code == 303 and resp.headers["location"] == "/parent/system/updates#downloader"


def test_the_updates_page_follows_the_downloader_update(authed_client, app, helper):
    """Each step shows while it runs, the page keeps polling, and pressing
    again does not start a second update; the result shows after."""
    config = app.state.config
    updates.request_update(config)
    (config.data_dir / updates.PROGRESS_FILENAME).write_text('{"stage": "Checking the new version"}')
    page = authed_client.get("/parent/system/updates").text
    assert "Checking the new version" in page and "data-downloader-poll" in page
    assert "You can leave this page" in page
    assert authed_client.get("/parent/system/updates/downloader.json").json() == {
        "running": True, "progress": "Checking the new version"}
    first = updates.pending_request(config)
    authed_client.post("/parent/system/diagnostics/update-downloader")
    assert updates.pending_request(config) == first

    updates.request_path(config).unlink()
    updates.result_path(config).write_text(json.dumps({
        "kind": "yt-dlp", "status": "kept", "from_version": "2026.3.17", "to_version": "2026.3.17",
        "message": "Kept 2026.3.17: the new 2026.8.19 could not download videos."}))
    page = authed_client.get("/parent/system/updates").text
    assert "could not download videos" in page and "Last update failed" not in page
    assert "data-downloader-poll" not in page
    assert authed_client.get("/parent/system/updates/downloader.json").json()["running"] is False


def test_latest_version_lookup_is_cached_and_survives_errors(monkeypatch):
    import app.services.safe_fetch as sf
    calls = []

    def fake_fetch(url, max_bytes, timeout=15, headers=None):
        calls.append(url)
        raise OSError("offline")
    monkeypatch.setattr(sf, "fetch_public", fake_fetch)
    updates._latest_cache.clear()
    assert updates.latest_ytdlp_version() is None
    assert updates.latest_ytdlp_version() is None
    assert len(calls) == 1
    updates._latest_cache.clear()
