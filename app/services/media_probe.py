from __future__ import annotations
"""Media probe service — ffprobe wrapper and ffmpeg decoder allow-list.

Runs `ffmpeg -decoders` once at startup to learn what this installed
ffmpeg can actually decode, then uses `ffprobe` to inspect uploads and
reject unsupported codecs with a clear conversion hint.
"""

import json
import logging
import re
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class ProbeResult:
    codec_name: str          # first video stream codec (e.g. "h264", "vp9", "av1")
    width: int
    height: int
    duration_seconds: float
    container: str
    audio_codec: str = ""    # first audio stream codec (e.g. "aac", "opus"); "" if silent
    fps: float = 0.0         # video frame rate, parsed from r_frame_rate


class ProbeError(Exception):
    """Raised when ffprobe fails to read the file at all (not a codec issue)."""


class UnsupportedCodec(Exception):
    """Raised when the probed video codec is not in this ffmpeg's decoder set."""

    def __init__(self, message: str, codec_name: str, conversion_hint: str):
        super().__init__(message)
        self.codec_name = codec_name
        self.conversion_hint = conversion_hint


_DECODER_LINE = re.compile(r"^\s*V[FSXBD.]{5}\s+(\S+)")


def _parse_fps(rate: str | None) -> float:
    """Parse an ffprobe ``r_frame_rate`` ("30000/1001", "25/1", "0/0")."""
    if not rate:
        return 0.0
    try:
        if "/" in rate:
            num, den = rate.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(rate)
    except (ValueError, ZeroDivisionError):
        return 0.0


class MediaProbeService:
    def __init__(self):
        self._video_decoders: set[str] | None = None

    def get_supported_video_decoders(self) -> set[str]:
        """Return the set of video decoder names this ffmpeg can use.

        Runs `ffmpeg -decoders` once per process and caches the result.
        """
        if self._video_decoders is not None:
            return self._video_decoders
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-decoders"],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            logger.warning("Failed to enumerate ffmpeg decoders: %s", e)
            self._video_decoders = set()
            return self._video_decoders

        decoders: set[str] = set()
        past_header = False
        for line in result.stdout.splitlines():
            if not past_header:
                if line.strip().startswith("------"):
                    past_header = True
                continue
            m = _DECODER_LINE.match(line)
            if m:
                decoders.add(m.group(1))
        self._video_decoders = decoders
        logger.info("Detected %d ffmpeg video decoders", len(decoders))
        return decoders

    def probe(self, path: Path) -> ProbeResult:
        """Run ffprobe and return stream metadata. Raises ProbeError on failure.

        Reads every stream (not just ``v:0``) so the first audio codec
        and the video frame rate come back too — both needed to decide
        whether a file already meets the client playback baseline.
        """
        try:
            result = subprocess.run(
                [
                    "ffprobe", "-v", "error",
                    "-show_entries",
                    "stream=codec_name,codec_type,width,height,duration,r_frame_rate",
                    "-show_entries", "format=format_name,duration",
                    "-of", "json",
                    str(path),
                ],
                capture_output=True, text=True, timeout=30,
            )
        except Exception as e:
            raise ProbeError(f"ffprobe failed to run: {e}")

        if result.returncode != 0:
            raise ProbeError(
                f"ffprobe failed (exit {result.returncode}): "
                f"{result.stderr.strip()[:500]}"
            )

        try:
            data = json.loads(result.stdout)
        except json.JSONDecodeError as e:
            raise ProbeError(f"ffprobe returned invalid JSON: {e}")

        streams = data.get("streams") or []
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
        if video is None:
            raise ProbeError("ffprobe found no video stream in the file")

        fmt = data.get("format") or {}
        container = (fmt.get("format_name") or "").split(",")[0]
        duration_str = video.get("duration") or fmt.get("duration") or "0"
        try:
            duration = float(duration_str)
        except (TypeError, ValueError):
            duration = 0.0

        return ProbeResult(
            codec_name=video.get("codec_name") or "unknown",
            width=int(video.get("width") or 0),
            height=int(video.get("height") or 0),
            duration_seconds=duration,
            container=container,
            audio_codec=(audio.get("codec_name") if audio else "") or "",
            fps=_parse_fps(video.get("r_frame_rate")),
        )

    def is_faststart(self, path: Path) -> bool:
        """True if the MP4 ``moov`` atom precedes ``mdat`` (progressive start).

        ffprobe won't report this directly, so scan the top-level atom
        boundaries. A file whose ``moov`` trails ``mdat`` must be fully
        downloaded before playback can begin — the opposite of what the
        baseline wants. Returns False on any read error or non-MP4 file
        (callers treat False as "needs the +faststart remux").
        """
        try:
            with open(path, "rb") as f:
                moov_pos = mdat_pos = None
                while True:
                    header = f.read(8)
                    if len(header) < 8:
                        break
                    size = int.from_bytes(header[:4], "big")
                    atype = header[4:8]
                    start = f.tell() - 8
                    if size == 1:                      # 64-bit extended size
                        ext = f.read(8)
                        if len(ext) < 8:
                            break
                        size = int.from_bytes(ext, "big")
                    elif size == 0:                    # atom runs to EOF
                        if atype == b"moov" and moov_pos is None:
                            moov_pos = start
                        elif atype == b"mdat" and mdat_pos is None:
                            mdat_pos = start
                        break
                    if atype == b"moov" and moov_pos is None:
                        moov_pos = start
                    elif atype == b"mdat" and mdat_pos is None:
                        mdat_pos = start
                    if moov_pos is not None and mdat_pos is not None:
                        break
                    if size < 8:                       # malformed; bail
                        break
                    f.seek(start + size)
            if moov_pos is None or mdat_pos is None:
                return False
            return moov_pos < mdat_pos
        except Exception:
            return False

    def validate(self, path: Path, original_filename: str | None = None) -> ProbeResult:
        """Probe the file and ensure its video codec is decodable.

        Raises ProbeError if ffprobe can't read the file at all.
        Raises UnsupportedCodec with a conversion hint if the codec is
        not in this ffmpeg's decoder set.
        """
        probe = self.probe(path)
        supported = self.get_supported_video_decoders()
        if supported and probe.codec_name not in supported:
            safe_name = original_filename or path.name
            quoted = shlex.quote(safe_name)
            hint = (
                f"This video uses the '{probe.codec_name}' codec, but your "
                f"installed ffmpeg cannot decode it. Convert it first with: "
                f"ffmpeg -i {quoted} -c:v libx264 -c:a aac converted.mp4 — "
                f"or install an ffmpeg build with {probe.codec_name} support."
            )
            raise UnsupportedCodec(hint, probe.codec_name, hint)
        return probe
