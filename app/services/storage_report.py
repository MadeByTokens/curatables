from __future__ import annotations
"""Storage report service — read-only disk usage reporting.

Separate from StorageService (which owns the write path) so the
report logic can be reused by the parent dashboard and future
file-management UIs without pulling in backend dependencies.
"""

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from app.repositories.video_repo import VideoRepository


_GB = 1_073_741_824

Status = Literal["ok", "warning", "blocked", "unknown"]


def get_disk_status_brief(data_dir: Path, min_free_bytes: int) -> dict:
    """Small read-only helper for the topnav status chip.

    Returns {"free_gb": float, "status": str} without database access,
    safe to call on every template render. On filesystem error the
    status is "unknown" rather than raising.
    """
    try:
        free = shutil.disk_usage(data_dir).free
    except OSError:
        return {"free_gb": 0.0, "status": "unknown"}
    if free < min_free_bytes:
        status: Status = "blocked"
    elif free < min_free_bytes * 2:
        status = "warning"
    else:
        status = "ok"
    return {"free_gb": free / _GB, "status": status}


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
    def __init__(self, data_dir: Path, min_free_bytes: int,
                 video_repo: VideoRepository):
        self.data_dir = data_dir
        self.min_free_bytes = min_free_bytes
        self.video_repo = video_repo

    def get_report(self) -> DiskReport:
        usage = shutil.disk_usage(self.data_dir)
        used_by_curatables = self.video_repo.sum_file_size()
        status = self._status(usage.free)
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

    def _status(self, free_bytes: int) -> Status:
        if free_bytes < self.min_free_bytes:
            return "blocked"
        if free_bytes < self.min_free_bytes * 2:
            return "warning"
        return "ok"
