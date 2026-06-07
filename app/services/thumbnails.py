from __future__ import annotations
"""Thumbnail service — download, extract, and manage video thumbnails."""

import logging
import subprocess
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)


class ThumbnailService:
    def __init__(self, data_dir: Path):
        self.data_dir = data_dir

    def get_path(self, video_id: str) -> Path:
        return self.data_dir / "videos" / video_id / "thumb.jpg"

    def is_cached(self, video_id: str) -> bool:
        return self.get_path(video_id).exists()

    def save_uploaded(self, video_id: str, file_data: bytes) -> Path:
        """Overwrite the canonical thumbnail for a video with uploaded
        bytes. The on-disk filename stays `thumb.jpg` regardless of the
        source format — the media route sniffs magic bytes and returns
        the correct Content-Type. Caller is responsible for having
        validated the payload."""
        thumb_path = self.get_path(video_id)
        thumb_path.parent.mkdir(parents=True, exist_ok=True)
        thumb_path.write_bytes(file_data)
        return thumb_path

    def download(self, video_id: str, thumbnail_url: str | None = None) -> Path | None:
        """Download a thumbnail and save locally. Returns path or None."""
        if self.is_cached(video_id):
            return self.get_path(video_id)

        if not thumbnail_url:
            return None

        thumb_path = self.get_path(video_id)
        thumb_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            req = urllib.request.Request(
                thumbnail_url, headers={"User-Agent": "Mozilla/5.0"}
            )
            with urllib.request.urlopen(req, timeout=15) as resp:
                with open(thumb_path, "wb") as f:
                    f.write(resp.read())
            return thumb_path
        except Exception as e:
            logger.warning("Error downloading thumbnail for %s: %s", video_id, e)
            return None

    def extract_frame(self, video_id: str, timestamp: str = "00:00:01",
                      source_path: Path | None = None) -> Path | None:
        """Extract a frame from a video using ffmpeg.

        By default looks for the downloaded-video path
        `{data_dir}/videos/{id}/video.mp4` (used for yt-dlp downloads).
        Pass `source_path` to extract from an arbitrary file (used by
        the upload ingest path to read from `uploads/{id}/video.<ext>`).
        """
        if source_path is None:
            source_path = self.data_dir / "videos" / video_id / "video.mp4"
        if not source_path.exists():
            return None

        thumb_path = self.get_path(video_id)
        thumb_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-ss", timestamp,
                    "-i", str(source_path),
                    "-vframes", "1",
                    "-q:v", "2",
                    str(thumb_path),
                ],
                capture_output=True, timeout=30,
            )
            return thumb_path if thumb_path.exists() else None
        except Exception as e:
            logger.warning("Error extracting frame for %s: %s", video_id, e)
            return None

    def delete(self, video_id: str) -> None:
        """Remove any thumbnails for this video.

        Covers the primary `videos/<id>/thumb.jpg` location as well as
        any custom uploaded thumbnail under `thumbnails/custom/<id>.*`.
        Silent on missing files.
        """
        thumb = self.get_path(video_id)
        try:
            thumb.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove thumbnail for %s: %s", video_id, e)

        custom_dir = self.data_dir / "thumbnails" / "custom"
        if custom_dir.exists():
            for entry in custom_dir.glob(f"{video_id}.*"):
                try:
                    entry.unlink(missing_ok=True)
                except OSError as e:
                    logger.warning("Could not remove custom thumbnail %s: %s", entry, e)

    def ensure_thumbnail(self, video_id: str, thumbnail_url: str | None = None) -> bool:
        """Try to get a thumbnail by any means available.
        Returns True if a thumbnail exists after this call."""
        if self.is_cached(video_id):
            return True
        if thumbnail_url:
            if self.download(video_id, thumbnail_url):
                return True
        # Last resort: extract from video file
        if self.extract_frame(video_id):
            return True
        return False
