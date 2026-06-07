"""Unit tests for VideoSourceService.parse_url — the platform-aware
URL classifier that sits between /parent/add and the yt-dlp backend.
"""

from app.services.video_source import ParsedURL, VideoSourceService


class _StubBackend:
    """Minimal stand-in so we can instantiate the service — parse_url
    itself never touches the backend."""
    last_error = None

    def fetch_video_info(self, url): return None
    def fetch_channel_videos(self, url, max_results=50): return ("", [])
    def fetch_playlist(self, url, max_results=50): return ("", [])
    def download_video(self, url, output_dir, resolution=720,
                       subtitle_langs=None): return None


def _svc():
    return VideoSourceService(_StubBackend())


class TestYouTubeFastPath:
    """Known YouTube URL shapes must still classify correctly — we
    rely on this fast path to avoid a network round-trip in the
    common case."""

    def test_standard_watch_url(self):
        parsed = _svc().parse_url("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert parsed == ParsedURL(
            url_type="video",
            resource_id="dQw4w9WgXcQ",
            clean_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )

    def test_youtu_be_short_url(self):
        parsed = _svc().parse_url("https://youtu.be/dQw4w9WgXcQ")
        assert parsed.url_type == "video"
        assert parsed.resource_id == "dQw4w9WgXcQ"

    def test_shorts_url(self):
        parsed = _svc().parse_url("https://www.youtube.com/shorts/abc12345678")
        assert parsed.url_type == "video"
        assert parsed.resource_id == "abc12345678"

    def test_playlist_url(self):
        parsed = _svc().parse_url(
            "https://www.youtube.com/playlist?list=PLabc123XYZ"
        )
        assert parsed.url_type == "playlist"
        assert parsed.resource_id == "PLabc123XYZ"

    def test_channel_handle_url(self):
        parsed = _svc().parse_url("https://www.youtube.com/@SomeChannel")
        assert parsed.url_type == "channel"
        assert parsed.resource_id == "@SomeChannel"

    def test_bare_handle_shorthand(self):
        parsed = _svc().parse_url("@SomeChannel")
        assert parsed.url_type == "channel"
        assert parsed.resource_id == "@SomeChannel"
        assert parsed.clean_url == "https://www.youtube.com/@SomeChannel"

    def test_channel_slash_channel_url(self):
        parsed = _svc().parse_url(
            "https://www.youtube.com/channel/UCabc123XYZ456"
        )
        assert parsed.url_type == "channel"
        assert "channel/UCabc123XYZ456" in parsed.resource_id


class TestCatchAllOtherHttpUrls:
    """Anything else that looks like an http(s) URL goes to the
    catch-all. The route layer hands it to yt-dlp, which resolves
    the real platform."""

    def test_vimeo_url(self):
        url = "https://vimeo.com/123456789"
        parsed = _svc().parse_url(url)
        assert parsed == ParsedURL(
            url_type="video",
            resource_id=url,
            clean_url=url,
        )

    def test_tiktok_url(self):
        url = "https://www.tiktok.com/@user/video/1234567890123456789"
        parsed = _svc().parse_url(url)
        assert parsed.url_type == "video"
        assert parsed.resource_id == url

    def test_dailymotion_url(self):
        url = "https://www.dailymotion.com/video/x2a3b4c"
        parsed = _svc().parse_url(url)
        assert parsed.url_type == "video"
        assert parsed.clean_url == url

    def test_peertube_url(self):
        url = "https://framatube.org/w/abcd1234"
        parsed = _svc().parse_url(url)
        assert parsed.url_type == "video"
        assert parsed.resource_id == url

    def test_twitter_url(self):
        url = "https://x.com/user/status/1234567890123456789"
        parsed = _svc().parse_url(url)
        assert parsed.url_type == "video"

    def test_http_scheme_also_works(self):
        # yt-dlp happily handles http:// URLs (some niche sites)
        parsed = _svc().parse_url("http://example.com/v/abc")
        assert parsed is not None
        assert parsed.url_type == "video"


class TestRejected:
    """Only genuinely non-URL strings should come back as None."""

    def test_empty_string(self):
        assert _svc().parse_url("") is None

    def test_whitespace_only(self):
        assert _svc().parse_url("   ") is None

    def test_plain_words(self):
        assert _svc().parse_url("hello world") is None

    def test_no_scheme(self):
        # "vimeo.com/123" without a scheme: we don't try to guess
        # whether the user meant http:// — return None and let the
        # form show "Could not recognize this URL."
        assert _svc().parse_url("vimeo.com/123") is None

    def test_none_input(self):
        assert _svc().parse_url(None) is None
