"""Unit tests for the Tier 1 iframe embed allow-list."""

import pytest

from app.services.embeds import embed_url_for


class TestTier1Platforms:
    def test_youtube(self):
        assert embed_url_for("youtube", "dQw4w9WgXcQ") == \
            "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ"

    def test_youtube_case_insensitive(self):
        # yt-dlp's extractor_key is "Youtube" (mixed case); we
        # lowercase internally so both "youtube" and "Youtube" work.
        assert embed_url_for("Youtube", "abc") == \
            "https://www.youtube-nocookie.com/embed/abc"

    def test_vimeo(self):
        assert embed_url_for("vimeo", "123456789") == \
            "https://player.vimeo.com/video/123456789"

    def test_dailymotion(self):
        assert embed_url_for("dailymotion", "x2a3b4c") == \
            "https://www.dailymotion.com/embed/video/x2a3b4c"

    def test_ted(self):
        assert embed_url_for("ted", "some_talk_slug") == \
            "https://embed.ted.com/talks/some_talk_slug"

    def test_peertube_happy_path(self):
        # PeerTube is federated — the embed URL is derived from the
        # video's webpage_url host.
        result = embed_url_for(
            "peertube",
            "abcd-1234-uuid",
            original_url="https://framatube.org/w/abcd-1234-uuid",
        )
        assert result == "https://framatube.org/videos/embed/abcd-1234-uuid"

    def test_peertube_uses_original_url_host(self):
        # Confirm the host is lifted from original_url rather than
        # guessed from a central list.
        result = embed_url_for(
            "peertube",
            "xyz",
            original_url="https://peertube.example.org/videos/watch/xyz",
        )
        assert result == "https://peertube.example.org/videos/embed/xyz"

    def test_peertube_missing_original_url_returns_none(self):
        assert embed_url_for("peertube", "abc", original_url="") is None

    def test_peertube_malformed_original_url_returns_none(self):
        assert embed_url_for("peertube", "abc", original_url="not-a-url") is None


class TestNonTier1Platforms:
    """Every platform NOT in the allow-list must return None so the
    template falls through to the link-out fallback card."""

    @pytest.mark.parametrize("extractor", [
        "tiktok",
        "twitter",
        "x",
        "instagram",
        "facebook",
        "reddit",
        "twitch",
        "TwitchClips",
        "bitchute",
        "odysee",
        "rumble",
        "soundcloud",
        "bandcamp",
        "",
    ])
    def test_returns_none(self, extractor):
        assert embed_url_for(extractor, "some-id", "https://example.com/v/1") is None

    def test_none_extractor(self):
        assert embed_url_for(None, "abc") is None
