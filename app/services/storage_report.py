"""Read-only disk usage reporting for the parent UI.

Kept apart from StorageService (the write path) so it needs no backend.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.repositories.video_repo import VideoRepository


_GB = 1_073_741_824

Status = Literal["ok", "warning", "blocked", "unknown"]


def disk_level(free: int, total: int, min_free_bytes: int, low_percent: int) -> Status:
    """'blocked' below the hard minimum (downloads and uploads stop),
    'warning' at low_percent free or less."""
    if free < min_free_bytes:
        return "blocked"
    if total > 0 and free * 100 <= total * low_percent:
        return "warning"
    return "ok"


def get_disk_status_brief(media_dir: Path, min_free_bytes: int, low_percent: int) -> dict:
    """Free space for the topnav chip and the dashboard. No database
    access, so it is cheap enough for every render. Status "unknown" on
    a filesystem error."""
    try:
        usage = shutil.disk_usage(media_dir)
    except OSError:
        return {"free_gb": 0.0, "free_percent": 0, "status": "unknown"}
    return {"free_gb": usage.free / _GB,
            "free_percent": round(usage.free * 100 / usage.total) if usage.total else 0,
            "status": disk_level(usage.free, usage.total, min_free_bytes, low_percent)}


# The SD card holds the system, the database and the nightly backups;
# under this much free space the box itself starts to fail.
SYSTEM_MIN_FREE_BYTES = 1 * _GB
SYSTEM_LOW_PERCENT = 10


def get_system_disk_status(data_dir: Path, media_dir: Path) -> dict | None:
    """Free space on the card that holds the database, or None when that
    is the videos drive itself (already covered by get_disk_status_brief)."""
    try:
        if data_dir.stat().st_dev == media_dir.stat().st_dev:
            return None
    except OSError:
        pass
    return get_disk_status_brief(data_dir, SYSTEM_MIN_FREE_BYTES, SYSTEM_LOW_PERCENT)


@dataclass
class DiskReport:
    total_bytes: int
    free_bytes: int
    used_bytes: int
    used_by_curatables_bytes: int
    min_free_bytes: int
    status: Status


@dataclass
class ChannelSize:
    channel_id: int | None
    channel_name: str
    video_count: int
    total_bytes: int


class StorageReportService:
    def __init__(self, media_dir: Path, min_free_bytes: int, low_percent: int,
                 video_repo: VideoRepository):
        self.media_dir = media_dir
        self.min_free_bytes = min_free_bytes
        self.low_percent = low_percent
        self.video_repo = video_repo

    def get_report(self) -> DiskReport:
        usage = shutil.disk_usage(self.media_dir)
        used_by_curatables = self.video_repo.sum_file_size()
        status = disk_level(usage.free, usage.total, self.min_free_bytes, self.low_percent)
        return DiskReport(
            total_bytes=usage.total,
            free_bytes=usage.free,
            used_bytes=usage.used,
            used_by_curatables_bytes=used_by_curatables,
            min_free_bytes=self.min_free_bytes,
            status=status,
        )

    def get_size_by_channel(self) -> list[ChannelSize]:
        rows = self.video_repo.size_by_channel()
        return [
            ChannelSize(channel_id=cid, channel_name=name,
                        video_count=count, total_bytes=total)
            for (cid, name, count, total) in rows
        ]
