"""Unit tests for the composite video-id helpers."""

import pytest

from app.services.ids import make_video_id, sanitize_id_component


class TestSanitizeIdComponent:
    @pytest.mark.parametrize("raw,expected", [
        ("dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("123456789", "123456789"),
        ("abc-def_ghi", "abc-def_ghi"),
        ("abc/def", "abc-def"),
        ("abc..def", "abc-def"),
        ("abc//def//ghi", "abc-def-ghi"),
        ("   whitespace  ", "-whitespace-"[:96]),  # spaces collapse
        ("", "x"),
        ("---", "x"),
        ("!!!", "x"),
        ("a" * 200, "a" * 96),  # length cap
    ])
    def test_sanitize_id_component(self, raw, expected):
        # The whitespace case is simpler than it looks: " " -> "-",
        # so "   whitespace   " -> "---whitespace---" -> collapsed to
        # "-whitespace-" -> stripped of trailing "-" on both ends to
        # "whitespace".
        if raw == "   whitespace  ":
            assert sanitize_id_component(raw) == "whitespace"
        else:
            assert sanitize_id_component(raw) == expected

    def test_none_input(self):
        assert sanitize_id_component(None) == "x"


class TestMakeVideoId:
    def test_youtube(self):
        assert make_video_id("youtube", "dQw4w9WgXcQ") == "youtube_dQw4w9WgXcQ"

    def test_vimeo(self):
        assert make_video_id("vimeo", "123456789") == "vimeo_123456789"

    def test_unknown_extractor_falls_back(self):
        assert make_video_id("", "abc") == "unknown_abc"

    def test_empty_raw_id_gets_x_placeholder(self):
        assert make_video_id("youtube", "") == "youtube_x"

    def test_unsafe_chars_in_raw_id_get_sanitised(self):
        # Path-traversal attempt: should never produce a string with
        # `..` or `/` in it.
        result = make_video_id("youtube", "../etc/passwd")
        assert ".." not in result
        assert "/" not in result
        assert result.startswith("youtube_")

    def test_unsafe_chars_in_extractor_get_sanitised(self):
        # Defensive: the extractor field is attacker-controllable
        # indirectly (yt-dlp's extractor_key for a new site).
        result = make_video_id("bad/extractor", "abc")
        assert "/" not in result
        assert result.endswith("_abc")

    def test_extractor_case_is_preserved(self):
        # sanitize_id_component does not lowercase — the caller
        # (ContentService / VideoMetadata) is responsible for lowering.
        # Confirm that so readers aren't surprised.
        assert make_video_id("YouTube", "abc") == "YouTube_abc"
