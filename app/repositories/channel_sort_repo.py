"""The order each kid picked for a channel's videos, and for All videos."""

from __future__ import annotations

from app.repositories.base import BaseRepository


class ChannelSortRepository(BaseRepository):

    def get(self, profile_id: int, channel_id: int) -> str | None:
        row = self.db.execute(
            "SELECT sort FROM profile_channel_sort WHERE profile_id = ? AND channel_id = ?",
            (profile_id, channel_id)).fetchone()
        return row["sort"] if row else None

    def set(self, profile_id: int, channel_id: int, sort: str | None) -> None:
        """Keep the kid's order for the channel; None goes back to newest
        first."""
        with self.db:
            if sort is None:
                self.db.execute(
                    "DELETE FROM profile_channel_sort WHERE profile_id = ? AND channel_id = ?",
                    (profile_id, channel_id))
            else:
                self.db.execute(
                    "INSERT INTO profile_channel_sort (profile_id, channel_id, sort) VALUES (?, ?, ?) "
                    "ON CONFLICT (profile_id, channel_id) DO UPDATE SET sort = excluded.sort",
                    (profile_id, channel_id, sort))

    def get_all(self, profile_id: int) -> str | None:
        row = self.db.execute("SELECT all_sort FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return row["all_sort"] or None if row else None

    def set_all(self, profile_id: int, sort: str | None) -> None:
        with self.db:
            self.db.execute("UPDATE profiles SET all_sort = ? WHERE id = ?", (sort or "", profile_id))
