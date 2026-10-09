from __future__ import annotations
"""Channel service — internal channel management."""

import builtins
import re
import shutil
from pathlib import Path

from app.models import Channel
from app.repositories import ChannelRepository
from app.services.images import validate_image

_COLOR_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
ASSET_KINDS = ("banner", "icon")


def valid_color(value: str | None) -> bool:
    return bool(value and _COLOR_RE.match(value))


class ChannelService:
    def __init__(self, channel_repo: ChannelRepository,
                 data_dir: Path | None = None):
        self.channel_repo = channel_repo
        self.data_dir = data_dir

    def asset_dir(self, channel_id: int) -> Path:
        if self.data_dir is None:
            raise RuntimeError("ChannelService has no data_dir")
        return self.data_dir / "channels" / str(channel_id)

    def save_asset(self, channel_id: int, kind: str, data: bytes) -> str:
        """Store a banner/icon image and return its filename.
        Raises ValueError if the bytes are not an accepted image."""
        if kind not in ASSET_KINDS:
            raise ValueError(f"Unknown channel asset: {kind}")
        ext = validate_image(data)
        channel_dir = self.asset_dir(channel_id)
        channel_dir.mkdir(parents=True, exist_ok=True)
        for old in channel_dir.glob(f"{kind}.*"):
            old.unlink(missing_ok=True)
        filename = f"{kind}{ext}"
        (channel_dir / filename).write_bytes(data)
        return filename

    def list(self) -> list[Channel]:
        return self.channel_repo.list()

    def get(self, channel_id: int) -> Channel | None:
        return self.channel_repo.get(channel_id)

    def create(self, name: str, description: str = "") -> int:
        return self.channel_repo.create(name, description)

    def create_for_kid(self, name: str, owner_profile_id: int) -> Channel:
        """Create a kid-owned channel. Raises ValueError on invalid name."""
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("Channel name is required.")
        if len(cleaned) > 64:
            raise ValueError("Channel name must be 64 characters or fewer.")
        if self.channel_repo.get_by_name(cleaned) is not None:
            raise ValueError("That name is already used. Try another one.")
        channel_id = self.channel_repo.create(
            cleaned, description="", owner_profile_id=owner_profile_id,
        )
        channel = self.channel_repo.get(channel_id)
        if channel is None:
            raise RuntimeError(f"Failed to read back created channel {channel_id}")
        return channel

    def visible_to_kid(self, profile_id: int) -> builtins.list[Channel]:
        """Return Channel objects visible to a kid profile."""
        ids = self.channel_repo.list_visible_to(profile_id)
        channels: builtins.list[Channel] = []
        for cid in ids:
            ch = self.channel_repo.get(cid)
            if ch is not None:
                channels.append(ch)
        channels.sort(key=lambda c: (c.position, c.name.lower()))
        return channels

    def name_taken(self, name: str, except_id: int | None = None) -> bool:
        other = self.channel_repo.get_by_name(name)
        return other is not None and other.id != except_id

    def update(self, channel_id: int, **fields) -> None:
        """Raises ValueError if a new name is already used by another channel."""
        if "name" in fields and self.name_taken(fields["name"], channel_id):
            raise ValueError(f'There is already a channel called "{fields["name"]}".')
        self.channel_repo.update(channel_id, **fields)

    def delete(self, channel_id: int, reassign_to: int | None = None) -> None:
        self.channel_repo.delete(channel_id, reassign_to=reassign_to)
        if self.data_dir is not None:
            shutil.rmtree(self.asset_dir(channel_id), ignore_errors=True)

    def count_videos(self, channel_id: int) -> int:
        return self.channel_repo.count_videos(channel_id)

    def list_with_counts(self) -> builtins.list[tuple[Channel, int]]:
        """Return channels with their video counts."""
        return self.channel_repo.list_with_counts()
