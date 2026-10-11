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


def test_yt_dlp_caches_in_the_data_folder(tmp_path):
    from app.config import Config
    config = Config()
    config.storage.path = str(tmp_path)
    opts = YtdlpBackend.from_config(config)._base_opts()
    assert opts["cachedir"] == str(tmp_path / "cache" / "yt-dlp")


def test_a_failed_subtitle_does_not_fail_the_video(monkeypatch, tmp_path):
    import yt_dlp
    runs = []

    class FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def download(self, urls):
            runs.append(self.opts.get("subtitleslangs"))
            if self.opts.get("writesubtitles"):
                raise yt_dlp.utils.DownloadError(
                    "ERROR: Unable to download video subtitles for 'pt': HTTP Error 429: Too Many Requests")
            (tmp_path / "v" / "video.mp4").write_bytes(b"x")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYDL)
    b = YtdlpBackend()
    monkeypatch.setattr(b, "_blocked", lambda url: False)
    out = b.download_video("https://www.youtube.com/watch?v=aaaaaaaaaaa", tmp_path / "v",
                           subtitle_langs=["en", "pt"])
    assert out == tmp_path / "v" / "video.mp4"
    assert runs == [["en", "pt"], None]



def test_too_many_requests_says_wait_not_change_settings():
    from app.template_utils import friendly_download_error
    e = YtdlpBackend().classify_error(Exception("ERROR: unable to download video data: HTTP Error 429: Too Many Requests"))
    assert e.error_type == BackendErrorType.RATE_LIMITED
    advice = friendly_download_error(f"[{e.error_type.value}] {e.message}")
    assert "Wait" in advice and "Impersonation" not in advice
