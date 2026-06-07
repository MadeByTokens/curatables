from __future__ import annotations
"""Data directory relocation service.

Moves the entire `~/curatables-data/` tree (or whatever the configured
data directory is) to a new path, updates the in-memory config, and
saves config.json at the new location. Runs a series of preflight
checks before doing anything and raises RelocationError with a clear
message on any refusal.
"""

import logging
import shutil
from pathlib import Path

from app.config import Config
from app.repositories import VideoRepository

logger = logging.getLogger(__name__)


_HEADROOM_BYTES = 100 * 1024 * 1024  # 100 MB for DB, logs, config


class RelocationError(Exception):
    """Raised when the data directory move cannot safely proceed."""


class RelocationService:
    def __init__(self, config: Config, video_repo: VideoRepository):
        self.config = config
        self.video_repo = video_repo

    def move(self, new_data_dir: str) -> Path:
        """Validate and move the data directory to a new path.

        Returns the resolved new Path on success. Raises
        RelocationError on any preflight failure.
        """
        old_path = Path(self.config.storage.path)
        target_raw = (new_data_dir or "").strip()
        if not target_raw:
            raise RelocationError("New data directory path is required.")

        new_path = Path(target_raw).expanduser()
        if not new_path.is_absolute():
            raise RelocationError(
                "New data directory must be an absolute path "
                "(starting with / on Linux or macOS)."
            )

        if new_path == old_path:
            raise RelocationError(
                "New data directory is the same as the current one."
            )

        # Target must not exist, or must be an empty directory.
        if new_path.exists():
            if not new_path.is_dir():
                raise RelocationError(
                    f"'{new_path}' already exists and is not a directory."
                )
            if any(new_path.iterdir()):
                raise RelocationError(
                    f"'{new_path}' already exists and is not empty. "
                    f"Choose an empty directory or a new path."
                )

        # Parent of the new path must exist and be writable.
        parent = new_path.parent
        if not parent.exists() or not parent.is_dir():
            raise RelocationError(
                f"Parent directory '{parent}' does not exist. "
                f"Create it first."
            )
        try:
            probe = parent / f".curatables_write_probe_{id(self)}"
            probe.touch()
            probe.unlink()
        except OSError as e:
            raise RelocationError(
                f"Cannot write to '{parent}': {e}. Check permissions."
            )

        # No in-flight downloads — background threads capture the old
        # db_path and would keep writing to the pre-move location.
        in_flight = self.video_repo.count_downloading()
        if in_flight:
            raise RelocationError(
                f"{in_flight} video(s) are currently downloading. "
                f"Wait for them to finish or cancel them, then try again."
            )

        # Enough space on the target filesystem for the video bytes
        # plus some headroom for the DB and logs.
        try:
            usage = shutil.disk_usage(parent)
        except OSError as e:
            raise RelocationError(
                f"Could not read free space on '{parent}': {e}"
            )
        required = self.video_repo.sum_file_size() + _HEADROOM_BYTES
        if usage.free < required:
            free_gb = usage.free / 1_073_741_824
            need_gb = required / 1_073_741_824
            raise RelocationError(
                f"Target filesystem has {free_gb:.1f} GB free but the move "
                f"needs at least {need_gb:.1f} GB. Free up space on the "
                f"target disk first."
            )

        # All checks passed — do the move.
        logger.info("Relocating data directory from %s to %s", old_path, new_path)
        shutil.move(str(old_path), str(new_path))

        # Update the in-memory config. New requests use get_db() which
        # reads config.data_dir fresh, so they pick up the new path
        # automatically. Then persist config.json at the new location.
        self.config.storage.path = str(new_path)
        self.config.save()
        logger.info("Relocation complete; config.json saved at %s",
                    new_path / "config.json")
        return new_path
