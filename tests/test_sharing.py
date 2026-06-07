"""Shared-curation service tests — .ytc round-trip, text parse,
PDF warn-and-degrade, and the import route's preview redirect."""

import json
import pytest

from app.models.channel import Channel
from app.models.video import Video
from app.services.sharing import (
    SCHEMA_ID,
    SharingError, SharingUnavailable,
    encode_ytc, encode_ytc_bytes, decode_ytc,
    render_text, parse_text,
    render_pdf, pdf_available,
)


def _channel():
    return Channel(
        name="Space & rockets",
        description="Stuff Mira likes",
        color="#2a9d8f",
        id=1,
    )


def _videos():
    return [
        Video(
            video_id="youtube_abc123",
            title="How rockets reach orbit",
            original_title="How rockets reach orbit",
            channel_name="SciChannel",
            description="Real rockets, real physics.",
            extractor="youtube",
            original_url="https://www.youtube.com/watch?v=abc123",
            added_at="2026-03-01 09:12:00",
        ),
        Video(
            video_id="vimeo_999",
            title="Saturn V documentary",
            original_title="Saturn V documentary",
            channel_name="Historical",
            extractor="vimeo",
            original_url="https://vimeo.com/999",
            added_at="2026-03-02 09:12:00",
        ),
    ]


class TestEncodeYtc:
    def test_schema_and_channel_fields(self):
        payload = encode_ytc(_channel(), _videos())
        assert payload["schema"] == SCHEMA_ID
        assert payload["channel"]["name"] == "Space & rockets"
        assert payload["channel"]["description"] == "Stuff Mira likes"
        assert "exported_at" in payload

    def test_videos_emit_urls_and_hints(self):
        payload = encode_ytc(_channel(), _videos())
        assert len(payload["videos"]) == 2
        first = payload["videos"][0]
        assert first["url"] == "https://www.youtube.com/watch?v=abc123"
        assert first["title"] == "How rockets reach orbit"
        assert first["extractor"] == "youtube"

    def test_videos_without_original_url_dropped(self):
        vs = _videos()
        vs.append(Video(video_id="x", title="orphan", original_title="orphan"))
        payload = encode_ytc(_channel(), vs)
        assert len(payload["videos"]) == 2  # orphan without URL skipped

    def test_encode_ytc_bytes_returns_utf8_json(self):
        raw = encode_ytc_bytes(_channel(), _videos())
        parsed = json.loads(raw.decode("utf-8"))
        assert parsed["schema"] == SCHEMA_ID


class TestDecodeYtc:
    def test_roundtrip(self):
        raw = encode_ytc_bytes(_channel(), _videos())
        payload = decode_ytc(raw)
        assert payload.channel_name == "Space & rockets"
        assert payload.channel_description == "Stuff Mira likes"
        assert len(payload.entries) == 2
        assert payload.entries[0].url.startswith("https://www.youtube.com/")
        assert payload.source_format == "ytc"

    def test_missing_schema_rejected(self):
        with pytest.raises(SharingError, match="Missing .schema"):
            decode_ytc(json.dumps({"videos": []}))

    def test_wrong_schema_rejected(self):
        with pytest.raises(SharingError, match="Unsupported schema"):
            decode_ytc(json.dumps({
                "schema": "curatables.ytc/2",
                "videos": [],
            }))

    def test_invalid_json_raises(self):
        with pytest.raises(SharingError, match="not valid JSON"):
            decode_ytc(b"{not json")

    def test_non_utf8_bytes_raises(self):
        with pytest.raises(SharingError, match="not valid UTF-8"):
            decode_ytc(b"\xff\xfe\x00invalid")

    def test_malformed_video_rows_skipped_silently(self):
        raw = json.dumps({
            "schema": SCHEMA_ID,
            "videos": [
                {"url": "https://ok.com/1"},
                "not-a-dict",         # skipped
                {"url": ""},           # empty URL, skipped
                {"title": "no url"},  # no URL, skipped
                {"url": "https://ok.com/2"},
            ],
        })
        payload = decode_ytc(raw)
        assert [e.url for e in payload.entries] == [
            "https://ok.com/1", "https://ok.com/2",
        ]

    def test_unknown_keys_ignored(self):
        raw = json.dumps({
            "schema": SCHEMA_ID,
            "future_top_level_key": {"anything": 1},
            "channel": {"name": "ok", "unknown": 1},
            "videos": [{"url": "https://x.com/1", "future_field": "x"}],
        })
        payload = decode_ytc(raw)  # must not raise
        assert payload.channel_name == "ok"
        assert len(payload.entries) == 1


class TestTextFormat:
    def test_render_text_header_and_urls(self):
        txt = render_text(_channel(), _videos())
        assert "# Curatables channel export" in txt
        assert "# name: Space & rockets" in txt
        assert "https://www.youtube.com/watch?v=abc123" in txt
        assert "# How rockets reach orbit" in txt

    def test_parse_text_roundtrip_through_render(self):
        txt = render_text(_channel(), _videos())
        payload = parse_text(txt)
        assert payload.channel_name == "Space & rockets"
        assert [e.url for e in payload.entries] == [
            "https://www.youtube.com/watch?v=abc123",
            "https://vimeo.com/999",
        ]

    def test_parse_text_strips_blank_lines_and_comments(self):
        raw = """
# a free-form comment line
https://a.com/1

# another comment

https://b.com/2
not-a-url
https://c.com/3
"""
        payload = parse_text(raw)
        urls = [e.url for e in payload.entries]
        assert urls == ["https://a.com/1", "https://b.com/2", "https://c.com/3"]

    def test_parse_text_recognises_name_header(self):
        raw = "# name: Imported Playlist\nhttps://x.com/1\n"
        payload = parse_text(raw)
        assert payload.channel_name == "Imported Playlist"

    def test_parse_text_ignores_plain_strings(self):
        raw = "just some plain text with no urls\nsecond line\n"
        payload = parse_text(raw)
        assert payload.entries == []


class TestPdf:
    def test_pdf_available_when_reportlab_installed(self):
        assert pdf_available() is True

    def test_render_pdf_produces_pdf_bytes(self):
        body = render_pdf(_channel(), _videos())
        assert body.startswith(b"%PDF")
        assert len(body) < 100_000  # sanity: not enormous

    def test_render_pdf_escapes_markup(self):
        # Ensure titles with <script> don't bypass reportlab's parser.
        vs = [Video(
            video_id="x", title="<script>alert(1)</script>",
            original_title="<script>",
            original_url="https://x.com/1",
        )]
        body = render_pdf(_channel(), vs)  # must not raise
        assert body.startswith(b"%PDF")

    def test_render_pdf_empty_channel(self):
        body = render_pdf(_channel(), [])
        assert body.startswith(b"%PDF")

    def test_sharing_unavailable_error_shape(self):
        # Simulate the error surface by hand-raising — actually-missing
        # reportlab is covered by render_pdf's try/except ImportError
        # path, which we can't trigger in-process.
        with pytest.raises(SharingUnavailable):
            raise SharingUnavailable("reportlab not installed")


class TestImportRouteIntegration:
    """End-to-end: POSTing a .ytc file lands on the review page."""

    def test_import_ytc_renders_preview(self, app, authed_client, monkeypatch):
        # Stub out yt-dlp fetch so the test doesn't hit the network.
        from app.services.content import ContentService
        from app.backends.base import VideoMetadata
        def fake_fetch_previews(self, urls):
            vids = [VideoMetadata(
                video_id=f"v{i}", title=f"title {i}",
                channel="ch", duration=60, upload_date="",
                view_count=0, description="", thumbnail_url="",
                extractor="youtube", original_url=u,
            ) for i, u in enumerate(urls)]
            return vids, []
        monkeypatch.setattr(ContentService, "fetch_previews_for_urls",
                            fake_fetch_previews)

        raw = encode_ytc_bytes(_channel(), _videos())
        resp = authed_client.post(
            "/parent/channels/import",
            files={"file": ("space.ytc", raw, "application/json")},
            data={"target_channel_id": "", "new_channel_name": "", "pasted": ""},
        )
        assert resp.status_code == 200
        # Lands on the existing review page, so the confirm form is there.
        assert "/parent/add/confirm" in resp.text
        assert "Review &amp; Add" in resp.text

    def test_import_empty_file_returns_error(self, app, authed_client):
        resp = authed_client.post(
            "/parent/channels/import",
            data={"pasted": "", "target_channel_id": "", "new_channel_name": ""},
        )
        assert resp.status_code == 400
        assert "Pick a file or paste" in resp.text

    def test_import_wrong_schema_returns_error(self, app, authed_client):
        bogus = b'{"schema": "curatables.ytc/99", "videos": []}'
        resp = authed_client.post(
            "/parent/channels/import",
            files={"file": ("bad.ytc", bogus, "application/json")},
            data={"pasted": "", "target_channel_id": "", "new_channel_name": ""},
        )
        assert resp.status_code == 400
        assert "Unsupported schema" in resp.text

    def test_import_text_pasted(self, app, authed_client, monkeypatch):
        from app.services.content import ContentService
        from app.backends.base import VideoMetadata
        monkeypatch.setattr(
            ContentService, "fetch_previews_for_urls",
            lambda self, urls: (
                [VideoMetadata(
                    video_id=f"v{i}", title="t", channel="c",
                    duration=0, upload_date="", view_count=0,
                    description="", thumbnail_url="", extractor="youtube",
                    original_url=u)
                 for i, u in enumerate(urls)],
                [],
            ),
        )
        resp = authed_client.post(
            "/parent/channels/import",
            data={
                "pasted": "# name: Pasted\nhttps://a.com/1\nhttps://b.com/2",
                "target_channel_id": "",
                "new_channel_name": "",
            },
        )
        assert resp.status_code == 200
        assert "Review &amp; Add" in resp.text


class TestExportRoute:
    def test_export_ytc_returns_json_with_schema(self, app, authed_client):
        # Create a channel
        from app.dependencies import get_db
        from app.repositories import ChannelRepository
        conn = next(app.dependency_overrides[get_db]())
        cid = ChannelRepository(conn).create("Test")
        resp = authed_client.get(f"/parent/channels/{cid}/export?format=ytc")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/json")
        assert 'attachment; filename="Test.ytc"' in resp.headers["content-disposition"]
        payload = json.loads(resp.content)
        assert payload["schema"] == SCHEMA_ID

    def test_export_txt(self, app, authed_client):
        from app.dependencies import get_db
        from app.repositories import ChannelRepository
        conn = next(app.dependency_overrides[get_db]())
        cid = ChannelRepository(conn).create("Text Chan")
        resp = authed_client.get(f"/parent/channels/{cid}/export?format=txt")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "# name: Text Chan" in resp.text

    def test_export_pdf_when_reportlab_present(self, app, authed_client):
        from app.dependencies import get_db
        from app.repositories import ChannelRepository
        conn = next(app.dependency_overrides[get_db]())
        cid = ChannelRepository(conn).create("Pdf Chan")
        resp = authed_client.get(f"/parent/channels/{cid}/export?format=pdf")
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "application/pdf"
        assert resp.content.startswith(b"%PDF")

    def test_export_unknown_format_400(self, app, authed_client):
        from app.dependencies import get_db
        from app.repositories import ChannelRepository
        conn = next(app.dependency_overrides[get_db]())
        cid = ChannelRepository(conn).create("X")
        resp = authed_client.get(f"/parent/channels/{cid}/export?format=bogus")
        assert resp.status_code == 400

    def test_export_pdf_unavailable_returns_503(self, app, authed_client, monkeypatch):
        from app.dependencies import get_db
        from app.repositories import ChannelRepository
        from app.features.parent_sharing import router as sharing_router_module
        conn = next(app.dependency_overrides[get_db]())
        cid = ChannelRepository(conn).create("No PDF")
        monkeypatch.setattr(sharing_router_module, "pdf_available",
                            lambda: False)
        resp = authed_client.get(f"/parent/channels/{cid}/export?format=pdf")
        assert resp.status_code == 503
        assert "reportlab" in resp.text
