"""Keep a minimum of free space on the videos drive while files are written.

StorageService.check_can_write refuses work that would not fit before it
starts. A SpaceWatch covers what happens after: several downloads,
uploads and conversions run at once and nobody knows the final size of a
download in advance, so each long write checks the drive every few
seconds and stops itself before free space drops under the minimum.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

_GB = 1024 ** 3


class InsufficientDiskSpace(Exception):
    """Raised when the drive cannot take this file and keep its minimum free."""


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def out_of_space_message(free: int, min_free: int) -> str:
    return (f"Insufficient disk space: {free / _GB:.1f} GB free, minimum "
            f"{min_free / _GB:.1f} GB required. Free some space (Storage), then retry.")


class SpaceWatch:
    """ok() is False once free space on `path` drops under `min_free`
    (re-measured at most every `every` seconds). Once tripped it stays
    tripped, so the caller can tell afterwards why its write stopped."""

    def __init__(self, path: Path, min_free: int, every: float = 2.0):
        self.path = path
        self.min_free = min_free
        self.every = every
        self.tripped = False
        self.free = -1
        self._checked = float("-inf")

    def ok(self) -> bool:
        if self.tripped:
            return False
        if self.min_free <= 0:
            return True
        now = time.monotonic()
        if now - self._checked >= self.every:
            self._checked = now
            try:
                self.free = free_bytes(self.path)
            except OSError:
                return True
            if self.free < self.min_free:
                self.tripped = True
        return not self.tripped

    def check(self) -> None:
        """Raise InsufficientDiskSpace if the drive is under its minimum."""
        if not self.ok():
            raise InsufficientDiskSpace(out_of_space_message(self.free, self.min_free))

    def message(self) -> str:
        return out_of_space_message(max(self.free, 0), self.min_free)
