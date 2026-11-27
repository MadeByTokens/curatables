"""What a failed download says: yt-dlp's message sorted into a kind, the
advice the parent reads, and YouTube's own words kept for Details."""

import pytest

from app.backends.base import BackendError, BackendErrorType
from app.backends.ytdlp import YtdlpBackend
from app.services.content import stored_error
from app.template_utils import friendly_download_error

GONE = BackendErrorType.VIDEO_UNAVAILABLE
REFUSED = BackendErrorType.REFUSED


@pytest.mark.parametrize("message, kind", [
    # The bare answer yt-dlp gives when YouTube refuses without a reason.
    ("ERROR: [youtube] abcdefghijk: This video is not available", REFUSED),
    ("ERROR: [youtube] abc: Video unavailable. This content isn't available.", REFUSED),
    ("ERROR: [youtube] abc: Video unavailable", REFUSED),
    ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access to this video", GONE),
    ("ERROR: [youtube] abc: Video unavailable. This video has been removed by the uploader", GONE),
    ("ERROR: [youtube] abc: Video unavailable. This video is no longer available because the YouTube "
     "account associated with this video has been terminated.", GONE),
    ("ERROR: [youtube] abc: The uploader has not made this video available in your country", GONE),
    ("ERROR: [youtube] abc: Join this channel to get access to members-only content like this video", GONE),
    ("ERROR: [youtube] abc: Requested format is not available. Use --list-formats", BackendErrorType.FORMAT),
    ("ERROR: unable to download video data: HTTP Error 503: Service Unavailable", BackendErrorType.NETWORK),
])
def test_yt_dlp_messages_are_sorted(message, kind):
    assert YtdlpBackend().classify_error(Exception(message)).error_type == kind


def test_a_refused_video_points_to_the_downloader_update():
    e = YtdlpBackend().classify_error(Exception("ERROR: [youtube] abc: This video is not available"))
    assert "downloader" in e.message and "Box → Updates" in e.suggestion
    advice = friendly_download_error(stored_error(e))
    assert "Update the downloader" in advice and "private" not in advice


def test_details_keep_what_youtube_said():
    e = BackendError(REFUSED, "YouTube didn't give the box this video.",
                     "\x1b[0;31mERROR:\x1b[0m [youtube] abcdefghijk: This video is not available")
    assert stored_error(e) == ("[refused] YouTube didn't give the box this video. "
                               "Source said: [youtube] abcdefghijk: This video is not available")


def test_details_are_cut_short_and_not_repeated():
    long = BackendError(GONE, "Gone.", "x" * 2000)
    assert len(stored_error(long)) < 450 and stored_error(long).endswith("…")
    assert stored_error(BackendError(GONE, "Gone.", "Gone.")) == "[unavailable] Gone."
    assert stored_error(BackendError(GONE, "Gone.", "")) == "[unavailable] Gone."
