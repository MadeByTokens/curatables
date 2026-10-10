"""Facts about the filesystem that holds the videos.

When the videos live on a USB drive (storage.media_path), the drive can
be missing (unplugged, or not mounted after a power cut), and a FAT32
drive cannot hold a file of 4 GB or more.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

FAT_FILE_LIMIT = 4 * 1024 ** 3 - 1
_FAT_TYPES = {"vfat", "msdos", "fat"}
_cache: dict[str, tuple[float, str]] = {}
_TTL = 30.0


def _unescape(field: str) -> str:
    return field.replace("\\040", " ").replace("\\011", "\t").replace("\\012", "\n").replace("\\134", "\\")


def filesystem_type(path: Path, mountinfo: str = "/proc/self/mountinfo") -> str:
    """The type of the filesystem holding path ("" if unknown)."""
    key = str(path)
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < _TTL:
        return hit[1]
    try:
        target = os.path.realpath(path)
        with open(mountinfo) as f:
            lines = f.read().splitlines()
    except OSError:
        return ""
    best, best_type = "", ""
    for line in lines:
        left, _, right = line.partition(" - ")
        fields = left.split()
        if len(fields) < 5 or not right:
            continue
        mount_point = _unescape(fields[4])
        prefix = mount_point.rstrip("/") + "/"
        if (target == mount_point or target.startswith(prefix) or mount_point == "/") \
                and len(mount_point) >= len(best):
            best, best_type = mount_point, right.split()[0]
    _cache[key] = (time.monotonic(), best_type)
    return best_type


def file_size_limit(media_dir: Path) -> int | None:
    """The largest file the videos folder can hold, or None for no limit."""
    return FAT_FILE_LIMIT if filesystem_type(media_dir) in _FAT_TYPES else None


def capped(media_dir: Path, limit: int) -> int:
    cap = file_size_limit(media_dir)
    return min(limit, cap) if cap else limit


def media_missing(media_path: str) -> bool:
    """True when the videos are configured to live on a drive that is not there."""
    return bool(media_path) and not Path(media_path).is_dir()


TOO_BIG_FOR_FAT = ("The videos drive is FAT32, which cannot hold files of 4 GB or more, "
                   "and this one is {size:.1f} GB. Format the drive as exFAT (Box, Drives) to fit it.")
DRIVE_MISSING = ("The videos drive is not connected. Plug it in and press Reconnect on the "
                 "Box, Drives page.")
