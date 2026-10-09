"""Parent dashboard: onboarding, content filters, retry, status polling."""

from unittest.mock import patch

from app.dependencies import get_db
from app.models import Video
from app.repositories import VideoRepository
from app.template_utils import friendly_download_error


def _video(app, video_id, **fields):
    conn = next(app.dependency_overrides[get_db]())
    VideoRepository(conn).insert(Video(video_id=video_id, title=video_id,
                                      original_title=video_id,
                                      original_url="https://example.com/x"))
    if fields:
        VideoRepository(conn).update(video_id, **fields)
    return conn


def test_dashboard_shows_getting_started_on_empty_install(authed_client):
    page = authed_client.get("/parent/").text
    assert "Getting started" in page
    assert "http://testserver/" in page


def test_content_filter_and_retry(app, authed_client):
    conn = _video(app, "yt_bad", download_status="error",
                  download_error="[bot_blocked] Sign in to confirm")
    _video(app, "yt_ok", download_status="ready")
    page = authed_client.get("/parent/content?status=error").text
    assert "yt_bad" in page and "yt_ok" not in page
    assert "The site blocked the download" in page
    with patch("app.services.content.ContentService._start_download") as start:
        resp = authed_client.post("/parent/content/yt_bad/retry")
    assert resp.status_code == 302
    start.assert_called_once()
    assert VideoRepository(conn).get("yt_bad").download_status == "pending"


def test_status_endpoint_reports_download_state(app, authed_client):
    _video(app, "yt_p", download_status="downloading")
    resp = authed_client.get("/parent/content/status?ids=yt_p,nope")
    assert resp.json() == {"yt_p": "downloading"}


def test_status_endpoint_requires_parent(client):
    assert client.get("/parent/content/status?ids=x").status_code == 302


def test_content_page_no_longer_meta_refreshes(app, authed_client):
    _video(app, "yt_q", download_status="pending")
    page = authed_client.get("/parent/content").text
    assert 'http-equiv="refresh"' not in page
    assert 'data-download-status="pending"' in page


def test_nav_marks_current_section(authed_client):
    page = authed_client.get("/parent/settings").text
    assert '<a href="/parent/settings" class="active" aria-current="page">' in page


def test_friendly_download_error():
    assert "disk is full" in friendly_download_error("[disk_full] no space")
    assert "Retry" in friendly_download_error("weird thing")
    assert friendly_download_error("") == ""


def test_security_headers(client):
    resp = client.get("/profiles")
    assert resp.headers["X-Content-Type-Options"] == "nosniff"
    assert resp.headers["X-Frame-Options"] == "DENY"
