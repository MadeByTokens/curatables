"""yt-dlp is never pointed at the home network (links in shared lists)."""

import pytest

from app.backends.base import BackendErrorType
from app.backends.ytdlp import YtdlpBackend


@pytest.mark.parametrize("url", ["http://192.168.1.1/admin?reboot=1", "http://10.0.0.5:8080/x?list=1",
                                 "http://127.0.0.1/", "http://[::1]/", "http://localhost/"])
def test_home_network_urls_are_refused_before_yt_dlp(url, monkeypatch, tmp_path):
    import yt_dlp

    def boom(*a, **k):
        raise AssertionError("yt-dlp must not be called")
    monkeypatch.setattr(yt_dlp, "YoutubeDL", boom)
    b = YtdlpBackend()
    assert b.fetch_video_info(url) is None
    assert b.last_error.error_type == BackendErrorType.BLOCKED
    assert b.fetch_playlist(url) == ("Unknown", [])
    assert b.fetch_channel_videos(url) == ("Unknown", [])
    assert b.download_video(url, tmp_path / "v") is None
    assert "home network" in b.last_error.message


def test_names_that_do_not_resolve_are_left_to_yt_dlp(monkeypatch):
    b = YtdlpBackend()
    assert not b._blocked("https://no-such-host.invalid/watch")


def test_a_cut_off_link_gets_a_plain_message():
    e = YtdlpBackend().classify_error(Exception(
        "[youtube:truncated_id] abc: Incomplete YouTube ID abc. URL https://www.youtube.com/watch?v=abc looks truncated."))
    assert "incomplete" in e.message and "Copy the whole address" in e.suggestion
