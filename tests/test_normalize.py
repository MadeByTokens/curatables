"""Phase 1 playback guard — prove the ingest-time normalizer actually
produces files in the client playback baseline.

The hermetic yt-dlp stub writes a 4-byte placeholder, which ffprobe
cannot read, so the smoke suite can't prove the normalizer. These tests
generate *real* tiny non-baseline media with ffmpeg, run them through
``MediaNormalizer``, and ffprobe-assert the result is H.264 + AAC,
≤720p, ≤30fps, MP4 +faststart.

Skipped automatically if ffmpeg/ffprobe aren't on PATH.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

from app.services.media_probe import MediaProbeService
from app.services.normalize import (
    MediaNormalizer, BASELINE_HEIGHT, BASELINE_FPS,
)

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed",
)


def _make(path: Path, *, size: str, rate: int, vcodec: str, acodec: str,
          faststart: bool, duration: int = 1) -> Path:
    """Synthesize a tiny clip with the given codecs/size/rate."""
    args = [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=size={size}:rate={rate}:duration={duration}",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
    ]
    if vcodec == "libx264":
        args += ["-c:v", "libx264", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", vcodec, "-b:v", "300k"]
    args += ["-c:a", acodec]
    if faststart:
        args += ["-movflags", "+faststart"]
    args.append(str(path))
    subprocess.run(args, check=True)
    return path


def _assert_baseline(probe: MediaProbeService, path: Path) -> None:
    assert path.name == "video.mp4"
    p = probe.probe(path)
    assert p.codec_name == "h264", f"video codec {p.codec_name}"
    assert p.audio_codec in ("aac", ""), f"audio codec {p.audio_codec}"
    assert 0 < p.height <= BASELINE_HEIGHT, f"height {p.height}"
    assert p.fps <= BASELINE_FPS + 0.5, f"fps {p.fps}"
    assert probe.is_faststart(path), "not faststart"


@pytest.fixture
def probe():
    return MediaProbeService()


@pytest.fixture
def normalizer(probe):
    return MediaNormalizer(probe)


def test_transcodes_vp9_opus_1080p60_to_baseline(tmp_path, probe, normalizer):
    """The core bug: VP9/Opus, oversized, high-fps → H.264/AAC ≤720p30."""
    src = _make(tmp_path / "video.webm", size="1920x1080", rate=60,
                vcodec="libvpx-vp9", acodec="libopus", faststart=False)
    res = normalizer.normalize(src)
    assert res.action == "transcode"
    _assert_baseline(probe, res.path)
    # The misleading .webm must be gone — only the baseline mp4 remains.
    assert not src.exists()


def test_remux_only_when_codecs_ok_but_not_faststart(tmp_path, probe, normalizer):
    """H.264/AAC ≤720p30 but moov-last → cheap remux, no re-encode."""
    src = _make(tmp_path / "video.mp4", size="640x480", rate=25,
                vcodec="libx264", acodec="aac", faststart=False)
    assert not probe.is_faststart(src)
    res = normalizer.normalize(src)
    assert res.action == "remux"
    _assert_baseline(probe, res.path)


def test_already_baseline_is_noop(tmp_path, probe, normalizer):
    src = _make(tmp_path / "video.mp4", size="640x480", rate=25,
                vcodec="libx264", acodec="aac", faststart=True)
    res = normalizer.normalize(src)
    assert res.action == "none"
    assert res.path == src


def test_downscales_oversized_h264(tmp_path, probe, normalizer):
    """1080p H.264 (in baseline codec, out of baseline resolution)."""
    src = _make(tmp_path / "video.mp4", size="1920x1080", rate=30,
                vcodec="libx264", acodec="aac", faststart=True)
    res = normalizer.normalize(src)
    assert res.action == "transcode"
    _assert_baseline(probe, res.path)


def test_unreadable_file_is_skipped_not_lost(tmp_path, normalizer):
    """The hermetic 4-byte placeholder must survive (download still 'ready')."""
    src = tmp_path / "video.mp4"
    src.write_bytes(b"FAKE")
    res = normalizer.normalize(src)
    assert res.action == "skipped"
    assert res.path == src
    assert src.exists()
