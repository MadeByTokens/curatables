from __future__ import annotations
"""Channel service — internal channel management."""

from app.models import Channel
from app.repositories import ChannelRepository


class ChannelService:
    def __init__(self, channel_repo: ChannelRepository):
        self.channel_repo = channel_repo

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
        channel_id = self.channel_repo.create(
            cleaned, description="", owner_profile_id=owner_profile_id,
        )
        channel = self.channel_repo.get(channel_id)
        if channel is None:
            raise RuntimeError(f"Failed to read back created channel {channel_id}")
        return channel

    def visible_to_kid(self, profile_id: int,
                       whitelist_ids: list[int] | None) -> list[Channel]:
        """Return Channel objects visible to a kid profile."""
        ids = self.channel_repo.list_visible_to(profile_id, whitelist_ids)
        channels: list[Channel] = []
        for cid in ids:
            ch = self.channel_repo.get(cid)
            if ch is not None:
                channels.append(ch)
        channels.sort(key=lambda c: (c.position, c.name.lower()))
        return channels

    def update(self, channel_id: int, **fields) -> None:
        self.channel_repo.update(channel_id, **fields)

    def delete(self, channel_id: int, reassign_to: int | None = None) -> None:
        self.channel_repo.delete(channel_id, reassign_to=reassign_to)

    def count_videos(self, channel_id: int) -> int:
        return self.channel_repo.count_videos(channel_id)

    def list_with_counts(self) -> list[tuple[Channel, int]]:
        """Return channels with their video counts."""
        return self.channel_repo.list_with_counts()
