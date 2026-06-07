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
satisfies the contract in ``docs/ui-and-playback-plan.md`` §2:

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
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.services.media_probe import MediaProbeService, ProbeError

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


class MediaNormalizer:
    """Ensures an ingested file ends up as a baseline ``video.mp4``."""

    def __init__(self, probe: MediaProbeService):
        self.probe = probe

    def normalize(self, path: Path) -> NormalizeResult:
        """Normalize ``path`` in place; the result lives at ``<dir>/video.mp4``.

        Never raises on media problems — a file that can't be probed or
        transcoded is left exactly as it was (action ``skipped``) so a
        download/upload still completes rather than vanishing. Callers
        should use ``result.path`` as the canonical file afterwards.
        """
        target = path.with_name("video.mp4")
        try:
            info = self.probe.probe(path)
        except ProbeError as e:
            logger.debug("normalize: cannot probe %s (%s) — leaving as-is", path, e)
            return NormalizeResult(path=path, action="skipped", reason=f"probe failed: {e}")

        vcodec_ok = (
            info.codec_name == BASELINE_VCODEC
            and 0 < info.height <= BASELINE_HEIGHT
            and info.fps <= BASELINE_FPS + _FPS_TOLERANCE
        )
        acodec_ok = info.audio_codec in _OK_AUDIO
        is_mp4 = path.suffix.lower() == ".mp4"
        faststart = is_mp4 and self.probe.is_faststart(path)

        if vcodec_ok and acodec_ok and faststart:
            return NormalizeResult(path=path, action="none", reason="already baseline")

        # Build the ffmpeg command. Copy whatever already conforms; only
        # re-encode the streams that don't.
        args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(path)]

        if vcodec_ok:
            args += ["-c:v", "copy"]
        else:
            args += [
                "-c:v", "libx264", "-profile:v", "main", "-level", "4.0",
                "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "23",
            ]
            vf = []
            if info.height > BASELINE_HEIGHT:
                vf.append(f"scale=-2:{BASELINE_HEIGHT}")
            if info.fps > BASELINE_FPS + _FPS_TOLERANCE:
                vf.append(f"fps={BASELINE_FPS}")
            if vf:
                args += ["-vf", ",".join(vf)]

        if info.audio_codec == "":
            pass  # silent video — nothing to map
        elif acodec_ok:
            args += ["-c:a", "copy"]
        else:
            args += ["-c:a", "aac", "-b:a", "128k"]

        args += ["-movflags", "+faststart"]

        only_faststart = vcodec_ok and acodec_ok  # streams fine, container wasn't
        action = "remux" if only_faststart else "transcode"

        # Encode to a sibling temp file, then atomically swap into place.
        tmp = path.with_name("video.norm.mp4")
        args.append(str(tmp))
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=3600)
        except Exception as e:
            logger.warning("normalize: ffmpeg failed to launch for %s: %s", path, e)
            tmp.unlink(missing_ok=True)
            return NormalizeResult(path=path, action="skipped", reason=f"ffmpeg launch: {e}")

        if proc.returncode != 0 or not tmp.exists():
            logger.warning(
                "normalize: ffmpeg %s failed for %s (exit %s): %s",
                action, path, proc.returncode, proc.stderr.strip()[:300],
            )
            tmp.unlink(missing_ok=True)
            return NormalizeResult(path=path, action="skipped",
                                   reason=f"ffmpeg exit {proc.returncode}")

        # Success: tmp -> video.mp4, and drop the original if it was a
        # different container (e.g. video.webm) so only the baseline remains.
        tmp.replace(target)
        if path != target:
            path.unlink(missing_ok=True)
        logger.info("normalize: %s %s -> %s", action, path.name, target.name)
        return NormalizeResult(path=target, action=action)
