from __future__ import annotations
"""Profile repository — child profiles and their channel permissions."""

import sqlite3
from app.models import Profile
from app.repositories.base import BaseRepository


class ProfileRepository(BaseRepository):

    def get(self, profile_id: int) -> Profile | None:
        row = self.db.execute(
            "SELECT * FROM profiles WHERE id = ?", (profile_id,)
        ).fetchone()
        if not row:
            return None
        profile = self._to_model(row)
        profile.allowed_channel_ids = self._get_channel_ids(profile_id)
        return profile

    def count(self) -> int:
        row = self.db.execute("SELECT COUNT(*) AS n FROM profiles").fetchone()
        return int(row["n"]) if row else 0

    def list(self) -> list[Profile]:
        rows = self.db.execute(
            "SELECT * FROM profiles ORDER BY name"
        ).fetchall()
        profiles = []
        for r in rows:
            p = self._to_model(r)
            p.allowed_channel_ids = self._get_channel_ids(p.id)
            profiles.append(p)
        return profiles

    def create(self, profile: Profile) -> int:
        cur = self.db.execute(
            "INSERT INTO profiles (name, display_name, pin, avatar, theme, search_mode) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (profile.name, profile.display_name, profile.pin,
             profile.avatar, profile.theme, profile.search_mode),
        )
        self.db.commit()
        profile_id = cur.lastrowid
        if profile.allowed_channel_ids:
            self._set_channel_ids(profile_id, profile.allowed_channel_ids)
        return profile_id

    _UPDATABLE_FIELDS = {
        "name", "display_name", "pin", "avatar", "theme", "search_mode",
    }

    def update(self, profile_id: int, **fields) -> None:
        channel_ids = fields.pop("allowed_channel_ids", None)
        if fields:
            bad = set(fields) - self._UPDATABLE_FIELDS
            if bad:
                raise ValueError(f"Invalid fields for profile update: {bad}")
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            values = list(fields.values())
            values.append(profile_id)
            self.db.execute(
                f"UPDATE profiles SET {set_clause} WHERE id = ?", values
            )
        if channel_ids is not None:
            self._set_channel_ids(profile_id, channel_ids)
        self.db.commit()

    def delete(self, profile_id: int) -> None:
        # profile_channels rows are removed automatically via ON DELETE CASCADE
        self.db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self.db.commit()

    def _get_channel_ids(self, profile_id: int) -> list[int]:
        rows = self.db.execute(
            "SELECT channel_id FROM profile_channels WHERE profile_id = ?",
            (profile_id,),
        ).fetchall()
        return [r["channel_id"] for r in rows]

    def _set_channel_ids(self, profile_id: int, channel_ids: list[int]) -> None:
        self.db.execute(
            "DELETE FROM profile_channels WHERE profile_id = ?", (profile_id,)
        )
        for cid in channel_ids:
            self.db.execute(
                "INSERT INTO profile_channels (profile_id, channel_id) VALUES (?, ?)",
                (profile_id, cid),
            )

    def _to_model(self, row: sqlite3.Row) -> Profile:
        d = dict(row)
        return Profile(
            id=d["id"],
            name=d["name"],
            pin=d.get("pin", ""),
            display_name=d.get("display_name", ""),
            avatar=d.get("avatar", "default"),
            theme=d.get("theme", "base"),
            search_mode=d.get("search_mode", "disabled"),
            created_at=d.get("created_at", ""),
        )
