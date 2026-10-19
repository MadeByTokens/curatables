"""YouTube channels the parent trusts, by channel ID: a family list, and
per kid, channels added for that kid or family ones switched off."""

from __future__ import annotations

import builtins
from dataclasses import dataclass

from app.repositories.base import BaseRepository


@dataclass
class TrustedChannel:
    channel_id: str
    name: str


class TrustedChannelRepository(BaseRepository):

    def family(self) -> builtins.list[TrustedChannel]:
        rows = self.db.execute(
            "SELECT channel_id, name FROM trusted_youtube_channels "
            "ORDER BY name COLLATE NOCASE, channel_id").fetchall()
        return [TrustedChannel(r["channel_id"], r["name"]) for r in rows]

    def add_family(self, channel_id: str, name: str) -> None:
        self.db.execute(
            "INSERT INTO trusted_youtube_channels (channel_id, name) VALUES (?, ?) "
            "ON CONFLICT (channel_id) DO UPDATE SET name = excluded.name",
            (channel_id, name))
        self._forget_kids(channel_id)
        self.db.commit()

    def remove_family(self, channel_id: str) -> None:
        self.db.execute("DELETE FROM trusted_youtube_channels WHERE channel_id = ?",
                        (channel_id,))
        self._forget_kids(channel_id)
        self.db.commit()

    def _forget_kids(self, channel_id: str) -> None:
        """Adding a channel for every kid, or taking it off for every kid,
        replaces what any one kid had for it."""
        self.db.execute("DELETE FROM profile_youtube_channels WHERE channel_id = ?",
                        (channel_id,))

    def extra_for(self, profile_id: int) -> builtins.list[TrustedChannel]:
        """Channels trusted for this kid only (not on the family list)."""
        rows = self.db.execute(
            "SELECT k.channel_id, k.name FROM profile_youtube_channels k "
            "WHERE k.profile_id = ? AND k.trusted = 1 AND k.channel_id NOT IN "
            "(SELECT channel_id FROM trusted_youtube_channels) "
            "ORDER BY k.name COLLATE NOCASE, k.channel_id", (profile_id,)).fetchall()
        return [TrustedChannel(r["channel_id"], r["name"]) for r in rows]

    def switched_off_for(self, profile_id: int) -> set[str]:
        """Family channels switched off for this kid."""
        rows = self.db.execute(
            "SELECT channel_id FROM profile_youtube_channels "
            "WHERE profile_id = ? AND trusted = 0", (profile_id,)).fetchall()
        return {r["channel_id"] for r in rows}

    def ids_for(self, profile_id: int) -> set[str]:
        """Every channel trusted for this kid: the family's, less the ones
        switched off for them, and their own."""
        rows = self.db.execute(
            "SELECT channel_id FROM trusted_youtube_channels WHERE channel_id NOT IN "
            "(SELECT channel_id FROM profile_youtube_channels "
            " WHERE profile_id = ? AND trusted = 0) "
            "UNION SELECT channel_id FROM profile_youtube_channels "
            "WHERE profile_id = ? AND trusted = 1", (profile_id, profile_id)).fetchall()
        return {r["channel_id"] for r in rows}

    def add_for(self, profile_id: int, channel_id: str, name: str) -> None:
        self.db.execute(
            "INSERT INTO profile_youtube_channels (profile_id, channel_id, name, trusted) "
            "VALUES (?, ?, ?, 1) ON CONFLICT (profile_id, channel_id) "
            "DO UPDATE SET name = excluded.name, trusted = 1",
            (profile_id, channel_id, name))
        self.db.commit()

    def remove_for(self, profile_id: int, channel_id: str) -> None:
        """Forget this kid's own entry for a channel (a family one is
        trusted for them again)."""
        self.db.execute(
            "DELETE FROM profile_youtube_channels WHERE profile_id = ? AND channel_id = ?",
            (profile_id, channel_id))
        self.db.commit()

    def set_switched_off(self, profile_id: int, off: set[str]) -> None:
        """Which family channels this kid doesn't get; the rest apply."""
        for ch in self.family():
            if ch.channel_id in off:
                self.db.execute(
                    "INSERT INTO profile_youtube_channels (profile_id, channel_id, name, trusted) "
                    "VALUES (?, ?, ?, 0) ON CONFLICT (profile_id, channel_id) "
                    "DO UPDATE SET trusted = 0",
                    (profile_id, ch.channel_id, ch.name))
            else:
                self.db.execute(
                    "DELETE FROM profile_youtube_channels "
                    "WHERE profile_id = ? AND channel_id = ? AND trusted = 0",
                    (profile_id, ch.channel_id))
        self.db.commit()
