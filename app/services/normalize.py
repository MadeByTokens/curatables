from __future__ import annotations
"""Media normalization — bring an ingested file to the client playback baseline.

Why this exists
---------------
Modern sources (YouTube especially) serve **VP9/AV1 video + Opus audio**.
Re-containering those streams into ``.mp4`` (what ``merge_output_format``
does) changes the extension but not the codecs — Safari and old devices
still can't decode them, so the kid gets a black screen or audio-only.

The fix is to *probe* every ingested file and, when it falls outside the
baseline, either **remux** it (cheap: faststart fix, no re-encode) or
**transcode** it (expensive: re-encode video and/or audio) so the result
satisfies the contract in ``docs/history/ui-and-playback-plan.md`` §2:

- Container: MP4 with ``+faststart`` (moov atom first → plays before full download)
- Video: H.264, ≤ 720p, ≤ 30 fps
- Audio: AAC-LC (or no audio at all)

This is deliberately a plain synchronous function called from the
background download thread (and the upload finalize path) — it is
queue-driven / bursty by construction (one transcode per ingest), which
fits the idle-box / thermal envelope of the target hardware. No
always-on worker.

Design note (probe gate is the throttle)
----------------------------------------
There is no config flag. Tightening the yt-dlp format string to prefer
``avc1``+``mp4a`` means H.264 is the common case, so most ingests only
need the cheap faststart remux (or nothing). Only genuinely non-baseline
files pay for a full transcode. A file ffprobe cannot read (e.g. the
4-byte placeholder the hermetic test stub writes) is left untouched.
"""

import logging
import time
from dataclasses import dataclass
from pathlib import Path

from app.services.ffmpeg_runner import (
    CancelToken, Cancelled, ProgressCallback, Stalled, run_ffmpeg, tail_log,
)
from app.services.media_probe import MediaProbeService, ProbeError, ProbeResult

logger = logging.getLogger(__name__)

BASELINE_VCODEC = "h264"
BASELINE_ACODEC = "aac"
BASELINE_HEIGHT = 720
BASELINE_FPS = 30
# r_frame_rate of 29.97 (30000/1001) is "30 fps" for our purposes.
_FPS_TOLERANCE = 0.5
# Audio codecs that need no transcode: AAC, or no audio stream at all.
_OK_AUDIO = {BASELINE_ACODEC, ""}


@dataclass
class NormalizeResult:
    path: Path           # final on-disk path (always ``<dir>/video.mp4`` on success)
    action: str          # "none" | "remux" | "transcode" | "skipped"
    reason: str = ""     # human-readable note (why skipped / what changed)
    kind: str = ""       # what the conversion did, e.g. "remux" or "transcode-1080"
    media_seconds: float = 0.0
    wall_seconds: float = 0.0


@dataclass
class NormalizePlan:
    """What normalize() would do to a file, without doing it."""
    action: str          # "none" | "remux" | "transcode"
    kind: str


def plan_for(info: ProbeResult, *, is_mp4: bool, faststart: bool,
             audio_index: int | None = None, burn_subtitle_index: int | None = None
             ) -> NormalizePlan:
    vcodec_ok = (
        info.codec_name == BASELINE_VCODEC
        and 0 < info.height <= BASELINE_HEIGHT
        and info.fps <= BASELINE_FPS + _FPS_TOLERANCE
        and burn_subtitle_index is None
    )
    audio_codec = info.audio_codec
    if audio_index is not None:
        audio_codec = next((a.codec for a in info.audio_streams if a.index == audio_index),
                           audio_codec)
    acodec_ok = audio_codec in _OK_AUDIO
    first_audio = info.audio_streams[0].index if info.audio_streams else None
    keeps_first_audio = audio_index is None or audio_index == first_audio
    if vcodec_ok and acodec_ok and is_mp4 and faststart and keeps_first_audio:
        return NormalizePlan("none", "none")
    if vcodec_ok and acodec_ok:
        return NormalizePlan("remux", "remux")
    if vcodec_ok:
        return NormalizePlan("transcode", "audio")
    return NormalizePlan("transcode", f"transcode-{info.height or 0}")


class MediaNormalizer:
    """Ensures an ingested file ends up as a baseline ``video.mp4``."""

    def __init__(self, probe: MediaProbeService):
        self.probe = probe

    def normalize(self, path: Path, *, on_progress: ProgressCallback | None = None,
                  cancel: CancelToken | None = None, audio_index: int | None = None,
                  burn_subtitle_index: int | None = None) -> NormalizeResult:
        """Normalize ``path`` in place; the result lives at ``<dir>/video.mp4``.

        Never raises on media problems — a file that can't be probed or
        transcoded is left exactly as it was (action ``skipped``) so a
        download/upload still completes rather than vanishing. Callers
        should use ``result.path`` as the canonical file afterwards.

        Raises Cancelled when ``cancel`` fires; the partial output is
        removed first and the input is left as it was.
        """
        target = path.with_name("video.mp4")
        result = self.convert(path, path.with_name("video.norm.mp4"), on_progress=on_progress,
                              cancel=cancel, audio_index=audio_index,
                              burn_subtitle_index=burn_subtitle_index)
        if result.action not in ("remux", "transcode"):
            return result
        # Success: drop the original if it was a different container
        # (e.g. video.webm) so only the baseline remains.
        result.path.replace(target)
        if path != target:
            path.unlink(missing_ok=True)
        result.path = target
        return result

    def convert(self, src: Path, out: Path, *, on_progress: ProgressCallback | None = None,
                cancel: CancelToken | None = None, audio_index: int | None = None,
                burn_subtitle_index: int | None = None) -> NormalizeResult:
        """Write a baseline copy of ``src`` to ``out``; ``src`` is never
        modified. With action ``none`` nothing is written and the result
        path is ``src``; with ``skipped`` nothing is left at ``out``.

        ``audio_index`` and ``burn_subtitle_index`` are absolute stream
        indexes from the probe: the audio track to keep (default: the
        first one) and a picture subtitle track to draw into the video.
        """
        try:
            info = self.probe.probe(src)
        except ProbeError as e:
            logger.debug("normalize: cannot probe %s (%s) — leaving as-is", src, e)
            return NormalizeResult(path=src, action="skipped", reason=f"probe failed: {e}")

        is_mp4 = src.suffix.lower() == ".mp4"
        faststart = is_mp4 and self.probe.is_faststart(src)
        plan = plan_for(info, is_mp4=is_mp4, faststart=faststart, audio_index=audio_index,
                        burn_subtitle_index=burn_subtitle_index)
        if plan.action == "none":
            return NormalizeResult(path=src, action="none", reason="already baseline",
                                   kind="none")

        args = self._ffmpeg_args(src, info, plan, audio_index, burn_subtitle_index)
        log_path = out.with_name(out.name + ".log")
        args.append(str(out))
        started = time.monotonic()
        try:
            code = run_ffmpeg(args, duration=info.duration_seconds, log_path=log_path,
                              on_progress=on_progress, cancel=cancel)
        except Cancelled:
            out.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
            raise
        except Stalled as e:
            logger.warning("normalize: ffmpeg %s stalled for %s: %s", plan.action, src, e)
            out.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
            return NormalizeResult(path=src, action="skipped", reason=f"ffmpeg {e}",
                                   kind=plan.kind)
        except OSError as e:
            logger.warning("normalize: ffmpeg failed to launch for %s: %s", src, e)
            out.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
            return NormalizeResult(path=src, action="skipped", reason=f"ffmpeg launch: {e}")
        wall = time.monotonic() - started

        if code != 0 or not out.exists():
            detail = tail_log(log_path, 300)
            logger.warning("normalize: ffmpeg %s failed for %s (exit %s): %s",
                           plan.action, src, code, detail)
            out.unlink(missing_ok=True)
            log_path.unlink(missing_ok=True)
            return NormalizeResult(path=src, action="skipped",
                                   reason=f"ffmpeg exit {code}: {detail}".strip(": "),
                                   kind=plan.kind)
        log_path.unlink(missing_ok=True)
        logger.info("normalize: %s %s -> %s", plan.action, src.name, out.name)
        return NormalizeResult(path=out, action=plan.action, kind=plan.kind,
                               media_seconds=info.duration_seconds, wall_seconds=wall)

    @staticmethod
    def _ffmpeg_args(path: Path, info: ProbeResult, plan: NormalizePlan,
                     audio_index: int | None, burn_subtitle_index: int | None) -> list[str]:
        """Map exactly one video and one audio stream; subtitle and data
        streams are dropped (subtitles are extracted separately)."""
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path)]
        video_ref = f"0:{info.video_index}" if info.video_index is not None else "0:v:0"
        vcodec_ok = plan.kind in ("remux", "audio")

        vf = []
        if not vcodec_ok:
            if info.height > BASELINE_HEIGHT:
                vf.append(f"scale=-2:{BASELINE_HEIGHT}")
            if info.fps > BASELINE_FPS + _FPS_TOLERANCE:
                vf.append(f"fps={BASELINE_FPS}")
        if burn_subtitle_index is not None:
            chain = f"[{video_ref}][0:{burn_subtitle_index}]overlay"
            if vf:
                chain += "," + ",".join(vf)
            args += ["-filter_complex", chain + "[v]", "-map", "[v]"]
        else:
            args += ["-map", video_ref]
            if vf:
                args += ["-vf", ",".join(vf)]

        if audio_index is not None:
            args += ["-map", f"0:{audio_index}"]
            audio_codec = next((a.codec for a in info.audio_streams
                                if a.index == audio_index), info.audio_codec)
        else:
            args += ["-map", "0:a:0?"]
            audio_codec = info.audio_codec
        args += ["-sn", "-dn"]

        if vcodec_ok:
            args += ["-c:v", "copy"]
        else:
            args += [
                "-c:v", "libx264", "-profile:v", "main", "-level", "4.0",
                "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
            ]

        if audio_codec in _OK_AUDIO and audio_codec:
            args += ["-c:a", "copy"]
        elif audio_codec:
            args += ["-c:a", "aac", "-b:a", "128k", "-ac", "2"]

        args += ["-movflags", "+faststart"]
        return args
