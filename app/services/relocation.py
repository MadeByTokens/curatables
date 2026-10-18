"""Data directory relocation service.

Moves the entire `~/curatables-data/` tree (or whatever the configured
data directory is) to a new path, updates the in-memory config, and
saves config.json at the new location. Runs a series of preflight
checks before doing anything and raises RelocationError with a clear
message on any refusal.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from pathlib import Path

from app.config import Config
from app.repositories import VideoRepository

logger = logging.getLogger(__name__)


_HEADROOM_BYTES = 100 * 1024 * 1024  # 100 MB for DB, logs, config


class RelocationError(Exception):
    """Raised when the data directory move cannot safely proceed."""


def _tree_stats(root: Path) -> tuple[int, int]:
    """(files, bytes) under root, not following links."""
    files = size = 0
    for dirpath, _dirs, names in os.walk(root):
        for name in names:
            path = Path(dirpath) / name
            files += 1
            if not path.is_symlink():
                size += path.stat().st_size
    return files, size


class RelocationService:
    def __init__(self, config: Config, video_repo: VideoRepository,
                 pinned: bool = False):
        self.config = config
        self.video_repo = video_repo
        self.pinned = pinned

    def move(self, new_data_dir: str) -> Path:
        """Validate and move the data directory to a new path.

        Returns the resolved new Path on success. Raises
        RelocationError on any preflight failure.
        """
        if self.pinned:
            raise RelocationError(
                "This server was started with a fixed data directory "
                "(--data-dir, e.g. by the systemd service), so it cannot be "
                "moved from here. To put the library on an external drive, "
                "run scripts/setup-external-drive.sh (see docs/deployment.md)."
            )
        if self.config.storage.media_path:
            raise RelocationError(
                "The videos live on a separate media drive "
                f"({self.config.storage.media_path}), so the library cannot be "
                "moved from here. Use scripts/setup-external-drive.sh instead "
                "(see docs/deployment.md)."
            )
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
        if old_path in new_path.parents:
            raise RelocationError(
                "New data directory cannot be inside the current one."
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
            ) from e

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
            ) from e
        required = self.video_repo.sum_file_size() + _HEADROOM_BYTES
        if usage.free < required:
            free_gb = usage.free / 1_073_741_824
            need_gb = required / 1_073_741_824
            raise RelocationError(
                f"Target filesystem has {free_gb:.1f} GB free but the move "
                f"needs at least {need_gb:.1f} GB. Free up space on the "
                f"target disk first."
            )

        logger.info("Relocating data directory from %s to %s", old_path, new_path)
        self._copy_then_switch(old_path, new_path)

        # get_db() reads config.data_dir per request, so updating the
        # in-memory config is enough to switch live requests over.
        self.config.storage.path = str(new_path)
        self.config.save()
        # Leave a pointer behind so a restart without --data-dir, which
        # looks in the old default location, finds the library.
        try:
            old_path.mkdir(parents=True, exist_ok=True)
            (old_path / "config.json").write_text(
                json.dumps({"storage": {"path": str(new_path)}}, indent=2))
        except OSError:
            logger.warning("Could not leave a pointer config at %s", old_path)
        logger.info("Relocation complete; config.json saved at %s",
                    new_path / "config.json")
        return new_path

    @staticmethod
    def _copy_then_switch(old_path: Path, new_path: Path) -> None:
        """Move the tree without ever leaving half of it in each place.
        On the same filesystem it is one rename. Otherwise it is copied
        and checked first; a copy that fails is removed (it would only
        take space), and the old tree is deleted only after the copy is
        complete."""
        created = not new_path.exists()
        try:
            os.rename(old_path, new_path)
            return
        except OSError:
            pass   # another filesystem (or a target dir that exists): copy
        try:
            shutil.copytree(old_path, new_path, symlinks=True, dirs_exist_ok=not created)
            if _tree_stats(new_path) != _tree_stats(old_path):
                raise OSError("the copy does not match the original")
        except OSError as e:
            if created:
                shutil.rmtree(new_path, ignore_errors=True)
            else:
                for entry in new_path.iterdir():
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
            raise RelocationError(
                f"The move did not finish ({e}). Nothing was changed: the library "
                f"is still at {old_path}, and the partial copy was removed.") from e
        shutil.rmtree(old_path, ignore_errors=True)
