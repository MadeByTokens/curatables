"""ffmpeg jobs report measured progress, and a cancel or a stall stops
the whole process group and leaves nothing behind."""
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.services.ffmpeg_runner import (
    CancelToken, Cancelled, RateEstimator, Stalled, run_ffmpeg, tail_log,
)
from app.services.media_probe import MediaProbeService
from app.services.normalize import MediaNormalizer

FAKE_FFMPEG = """#!/bin/sh
sleep 1000 &
echo $! > "{pidfile}"
echo "fake ffmpeg starting" >&2
t=0
while [ $t -lt {steps} ]; do
  t=$((t + 1))
  echo "out_time_us=$((t * {step_us}))"
  echo "speed=2x"
  echo "progress=continue"
  sleep 0.2
done
{tail}
"""


def _fake(tmp_path, *, steps=1000, step_us=1_000_000, tail="echo progress=end; exit 0"):
    pidfile = tmp_path / "child.pid"
    script = tmp_path / "ffmpeg"
    script.write_text(FAKE_FFMPEG.format(pidfile=pidfile, steps=steps,
                                         step_us=step_us, tail=tail))
    script.chmod(0o755)
    return script, pidfile


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split()[2] != "Z"
    except OSError:
        return False


def test_reports_progress_and_finishes(tmp_path):
    script, _ = _fake(tmp_path, steps=5, step_us=2_000_000)
    seen = []
    code = run_ffmpeg([str(script)], duration=10.0, log_path=tmp_path / "log",
                      on_progress=seen.append)
    assert code == 0
    assert seen[-1].done == pytest.approx(10.0)
    assert seen[-1].fraction == pytest.approx(1.0)
    assert any(p.speed for p in seen)
    assert "fake ffmpeg starting" in tail_log(tmp_path / "log")


def test_cancel_stops_ffmpeg_and_its_children(tmp_path):
    script, pidfile = _fake(tmp_path)
    token = CancelToken()
    threading.Timer(1.0, token.cancel).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run_ffmpeg([str(script)], duration=1000.0, log_path=tmp_path / "log", cancel=token)
    assert time.monotonic() - started < 10
    child = int(pidfile.read_text())
    deadline = time.monotonic() + 3
    while _alive(child) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not _alive(child)


def test_already_cancelled_never_starts(tmp_path):
    script, pidfile = _fake(tmp_path)
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        run_ffmpeg([str(script)], duration=10.0, log_path=tmp_path / "log", cancel=token)
    assert not pidfile.exists()


def test_stall_is_detected_and_stopped(tmp_path):
    script, pidfile = _fake(tmp_path, steps=1, tail="sleep 1000")
    with pytest.raises(Stalled):
        run_ffmpeg([str(script)], duration=100.0, log_path=tmp_path / "log",
                   stall_seconds=1.5)
    assert not _alive(int(pidfile.read_text()))


def test_rate_uses_the_recent_window():
    now = [0.0]
    est = RateEstimator(window=60, clock=lambda: now[0])
    for t in range(0, 200, 10):
        now[0] = float(t)
        est.add(t * (0.5 if t < 100 else 2.0))
    assert est.rate() == pytest.approx(2.0)
    assert est.eta(done=380.0, total=580.0) == pytest.approx(100.0)


needs_ffmpeg = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed")


def _mkv_with_tracks(path: Path) -> Path:
    srt = path.with_name("in.srt")
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n")
    subprocess.run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=25:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
        "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
        "-i", str(srt), "-i", str(srt),
        "-map", "0", "-map", "1", "-map", "2", "-map", "3", "-map", "4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-c:s:0", "srt", "-c:s:1", "ass",
        "-metadata:s:a:0", "language=eng", "-metadata:s:a:1", "language=por",
        "-metadata:s:a:1", "title=Portuguese",
        "-metadata:s:s:0", "language=eng", "-metadata:s:s:1", "language=spa",
        str(path)], check=True)
    return path


@needs_ffmpeg
def test_probe_lists_audio_and_subtitle_tracks(tmp_path):
    info = MediaProbeService().probe(_mkv_with_tracks(tmp_path / "video.mkv"))
    assert [a.language for a in info.audio_streams] == ["eng", "por"]
    assert [(s.language, s.codec, s.is_text) for s in info.subtitle_streams] == [
        ("eng", "subrip", True), ("spa", "ass", True)]


@needs_ffmpeg
def test_picks_the_chosen_audio_and_drops_subtitle_streams(tmp_path):
    probe = MediaProbeService()
    src = _mkv_with_tracks(tmp_path / "video.mkv")
    portuguese = probe.probe(src).audio_streams[1].index
    seen = []
    res = MediaNormalizer(probe).normalize(src, audio_index=portuguese,
                                           on_progress=seen.append)
    assert res.action == "remux" and res.path.name == "video.mp4"
    out = probe.probe(res.path)
    assert [a.language for a in out.audio_streams] == ["por"]
    assert out.subtitle_streams == []
    assert not (tmp_path / "ffmpeg.log").exists()
    assert seen


def test_picture_subtitles_are_burned_in_through_an_overlay():
    from app.services.media_probe import ProbeResult, StreamInfo
    from app.services.normalize import plan_for
    info = ProbeResult(codec_name="h264", width=1920, height=1080, duration_seconds=60,
                       container="matroska", audio_codec="ac3", fps=24, video_index=0,
                       audio_streams=[StreamInfo(1, "ac3")],
                       subtitle_streams=[StreamInfo(2, "dvd_subtitle")])
    plan = plan_for(info, is_mp4=False, faststart=False, burn_subtitle_index=2)
    args = MediaNormalizer._ffmpeg_args(Path("video.mkv"), info, plan, None, 2)
    assert plan.action == "transcode"
    assert args[args.index("-filter_complex") + 1] == "[0:0][0:2]overlay,scale=-2:720[v]"
    assert args[args.index("-map") + 1] == "[v]"
    assert "-sn" in args and args[args.index("-c:v") + 1] == "libx264"
    assert args[args.index("-c:a") + 1] == "aac"


@needs_ffmpeg
def test_cancelled_conversion_leaves_the_input_untouched(tmp_path):
    probe = MediaProbeService()
    src = tmp_path / "video.webm"
    subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                    "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=60",
                    "-c:v", "libvpx-vp9", "-deadline", "realtime", "-cpu-used", "8",
                    "-b:v", "100k", str(src)], check=True)
    before = src.read_bytes()
    token = CancelToken()

    def cancel_once_running(progress):
        if progress.done > 0:
            token.cancel()

    with pytest.raises(Cancelled):
        MediaNormalizer(probe).normalize(src, cancel=token, on_progress=cancel_once_running)
    assert src.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["video.webm"]
