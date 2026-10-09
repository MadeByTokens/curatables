"""Channel repository — internal (parent-defined) channels."""
from __future__ import annotations

import builtins
import sqlite3
from app.models import Channel
from app.repositories.base import BaseRepository


def kid_sees_channel(profile: str, channel: str) -> str:
    """SQL condition: the kid in profiles row `profile` may see the
    channels row `channel`: channels the kid owns, plus every
    parent-created channel when unrestricted, or only the ones listed
    in profile_channels (which may include a sibling's) when restricted."""
    return (f"({channel}.owner_profile_id = {profile}.id"
            f" OR ({profile}.channels_restricted = 0"
            f" AND {channel}.owner_profile_id IS NULL)"
            f" OR ({profile}.channels_restricted = 1 AND EXISTS ("
            f"SELECT 1 FROM profile_channels pc"
            f" WHERE pc.profile_id = {profile}.id"
            f" AND pc.channel_id = {channel}.id)))")


class ChannelRepository(BaseRepository):

    def get(self, channel_id: int) -> Channel | None:
        row = self.db.execute(
            "SELECT * FROM channels WHERE id = ?", (channel_id,)
        ).fetchone()
        if not row:
            return None
        return self._to_model(row)

    def get_by_name(self, name: str) -> Channel | None:
        row = self.db.execute(
            "SELECT * FROM channels WHERE name = ?", (name,)
        ).fetchone()
        return self._to_model(row) if row else None

    def list(self) -> list[Channel]:
        rows = self.db.execute(
            "SELECT * FROM channels ORDER BY position, name"
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def create(self, name: str, description: str = "",
               owner_profile_id: int | None = None) -> int:
        """Create a channel. Returns ID (existing if duplicate name).

        When owner_profile_id is None the channel is parent-created
        (visible to all profiles subject to their whitelist). When set,
        the channel is owned by that kid profile and only visible to
        them until the parent shares it.
        """
        try:
            cur = self.db.execute(
                "INSERT INTO channels (name, description, owner_profile_id) "
                "VALUES (?, ?, ?)",
                (name, description, owner_profile_id),
            )
            self.db.commit()
            return self._new_id(cur)
        except sqlite3.IntegrityError:
            row = self.db.execute(
                "SELECT id FROM channels WHERE name = ?", (name,)
            ).fetchone()
            return int(row["id"])

    _UPDATABLE_FIELDS = {
        "name", "description", "position", "owner_profile_id",
        "banner_filename", "icon_filename", "color",
    }

    def update(self, channel_id: int, **fields) -> None:
        if not fields:
            return
        bad = set(fields) - self._UPDATABLE_FIELDS
        if bad:
            raise ValueError(f"Invalid fields for channel update: {bad}")
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values())
        values.append(channel_id)
        self.db.execute(
            f"UPDATE channels SET {set_clause} WHERE id = ?", values
        )
        self.db.commit()

    def count_videos(self, channel_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) as cnt FROM videos WHERE channel_id = ? AND status = 'active'",
            (channel_id,),
        ).fetchone()
        return int(row["cnt"])

    def list_with_counts(self) -> builtins.list[tuple[Channel, int]]:
        """List all channels with their active video counts in a single query."""
        rows = self.db.execute(
            """SELECT c.*, COUNT(v.video_id) as video_count
               FROM channels c
               LEFT JOIN videos v ON v.channel_id = c.id AND v.status = 'active'
               GROUP BY c.id
               ORDER BY c.position, c.name"""
        ).fetchall()
        return [(self._to_model(r), r["video_count"]) for r in rows]

    def delete(self, channel_id: int, reassign_to: int | None = None) -> None:
        """Delete a channel.

        When `reassign_to` is an int, videos previously in this
        channel move to that channel instead of being orphaned to
        channel_id=NULL. The caller is responsible for verifying
        reassign_to != channel_id and that the target exists.
        """
        if reassign_to is not None:
            self.db.execute(
                "UPDATE videos SET channel_id = ? WHERE channel_id = ?",
                (reassign_to, channel_id),
            )
        else:
            self.db.execute(
                "UPDATE videos SET channel_id = NULL WHERE channel_id = ?",
                (channel_id,),
            )
        self.db.execute("DELETE FROM channels WHERE id = ?", (channel_id,))
        self.db.commit()

    def _to_model(self, row: sqlite3.Row) -> Channel:
        d = dict(row)
        return Channel(
            id=d["id"],
            name=d["name"],
            description=d.get("description", ""),
            position=d.get("position", 0),
            created_at=d.get("created_at", ""),
            owner_profile_id=d.get("owner_profile_id"),
            banner_filename=d.get("banner_filename") or "",
            icon_filename=d.get("icon_filename") or "",
            color=d.get("color") or "#2a9d8f",
        )

    def list_owned_by(self, profile_id: int) -> builtins.list[Channel]:
        """Return channels owned by the given profile."""
        rows = self.db.execute(
            "SELECT * FROM channels WHERE owner_profile_id = ? "
            "ORDER BY position, name",
            (profile_id,),
        ).fetchall()
        return [self._to_model(r) for r in rows]

    def count_owned_by(self, profile_id: int) -> int:
        row = self.db.execute(
            "SELECT COUNT(*) AS cnt FROM channels WHERE owner_profile_id = ?",
            (profile_id,),
        ).fetchone()
        return int(row["cnt"])

    def list_visible_to(self, profile_id: int) -> builtins.list[int]:
        """IDs of the channels the kid profile may see (see kid_sees_channel)."""
        rows = self.db.execute(
            "SELECT ch.id FROM channels ch JOIN profiles a ON a.id = ? "
            f"WHERE {kid_sees_channel('a', 'ch')} ORDER BY ch.id",
            (profile_id,),
        ).fetchall()
        return [int(r["id"]) for r in rows]
