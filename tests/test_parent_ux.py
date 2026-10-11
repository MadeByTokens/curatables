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
    csp = resp.headers["Content-Security-Policy"]
    assert "script-src 'self';" in csp
    assert "unsafe-inline" not in csp.split("script-src", 1)[1].split(";", 1)[0]


def test_templates_have_no_inline_scripts_or_handlers():
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "app" / "templates"
    offenders = []
    for tpl in root.rglob("*.html"):
        text = tpl.read_text()
        if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", text):
            offenders.append(f"{tpl.name}: inline <script>")
        if re.search(r"\son[a-z]+\s*=\s*[\"']", text):
            offenders.append(f"{tpl.name}: inline event handler")
        if "javascript:" in text:
            offenders.append(f"{tpl.name}: javascript: URL")
    assert offenders == []


def test_kid_qr_code_is_parent_only_svg(client, authed_client):
    assert client.get("/parent/kid-qr.svg").status_code in (302, 303)
    resp = authed_client.get("/parent/kid-qr.svg")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in resp.content
    page = authed_client.get("/parent/profiles/")
    assert 'src="/parent/kid-qr.svg"' in page.text
    assert "Set up a tablet" in page.text


def test_kid_address_replaces_loopback_with_lan_name(app):
    from starlette.requests import Request
    from app.web import kid_address

    def req(host):
        return Request({"type": "http", "app": app, "scheme": "http", "path": "/",
                        "server": (host, 8080), "headers": [(b"host", f"{host}:8080".encode())]})

    app.state.config.server.mdns_enabled = True
    app.state.config.server.mdns_name = "Curatables"
    assert kid_address(req("127.0.0.1")) == "http://curatables.local:8080/"
    assert kid_address(req("192.0.2.9")) == "http://192.0.2.9:8080/"


def _fake_playlist(n):
    from app.backends.base import VideoMetadata
    return ("Songs", [VideoMetadata(video_id=f"vid{i:08d}x", title=f"Song {i}", channel="Ch",
                                    duration=60, view_count=0, upload_date="", description="",
                                    thumbnail_url="", extractor="youtube")
                      for i in range(n)])


def test_playlist_preview_shows_review_progress(authed_client):
    from unittest.mock import patch
    with patch("app.services.content.ContentService.fetch_preview",
               return_value=_fake_playlist(3)):
        resp = authed_client.post("/parent/add", data={
            "url": "https://www.youtube.com/playlist?list=PL1234567890abcdef"})
    assert resp.status_code == 200
    assert 'id="review-progress" data-total="3"' in resp.text
    assert 'id="next-unwatched"' in resp.text
    js = authed_client.get("/static/parent/content_preview.js").text
    assert "watched video(s)" in js


def test_settings_page_has_section_nav(authed_client):
    page = authed_client.get("/parent/settings").text
    for anchor in ("settings-storage", "settings-password", "settings-advanced"):
        assert f'href="#{anchor}"' in page and f'id="{anchor}"' in page
    assert page.index('id="settings-password"') < page.index('id="settings-advanced"')


def test_an_empty_filter_tab_does_not_say_the_library_is_empty(authed_client):
    assert "No videos yet" in authed_client.get("/parent/content").text
    page = authed_client.get("/parent/content", params={"status": "pending"}).text
    assert "No videos yet" not in page and "Nothing here right now" in page
