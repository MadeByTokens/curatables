"""Profile repository: kid profiles and their channel lists."""

from __future__ import annotations

import builtins
import sqlite3
from typing import Any

from app.models import Profile
from app.models.profile import FEATURE_SWITCHES
from app.repositories.base import BaseRepository

FEATURE_FIELDS = tuple(field for field, _label, _default in FEATURE_SWITCHES)


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
            p.allowed_channel_ids = self._get_channel_ids(r["id"])
            profiles.append(p)
        return profiles

    def create(self, profile: Profile) -> int:
        cur = self.db.execute(
            "INSERT INTO profiles (name, display_name, pin, pin_style, language, avatar, "
            "theme, search_mode, channels_restricted, auto_add_channel_id, auto_add_mode, "
            "trusted_only, " + ", ".join(FEATURE_FIELDS) + ") "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?" + ", ?" * len(FEATURE_FIELDS) + ")",
            (profile.name, profile.display_name, profile.pin, profile.pin_style,
             profile.language,
             profile.avatar, profile.theme, profile.search_mode,
             int(profile.channels_restricted or bool(profile.allowed_channel_ids)),
             profile.auto_add_channel_id, profile.auto_add_mode, profile.trusted_only,
             *(int(bool(getattr(profile, f))) for f in FEATURE_FIELDS)),
        )
        profile_id = self._new_id(cur)
        if profile.allowed_channel_ids:
            self._set_channel_ids(profile_id, profile.allowed_channel_ids)
        self.db.commit()
        return profile_id

    _UPDATABLE_FIELDS = {
        "name", "display_name", "pin", "pin_style", "language", "avatar", "theme",
        "search_mode", "auto_add_channel_id", "auto_add_mode", "trusted_only",
        "channels_restricted", *FEATURE_FIELDS,
    }

    def update(self, profile_id: int, **fields) -> None:
        channel_ids = fields.pop("allowed_channel_ids", None)
        if channel_ids is not None and "channels_restricted" not in fields:
            fields["channels_restricted"] = bool(channel_ids)
        if fields:
            bad = set(fields) - self._UPDATABLE_FIELDS
            if bad:
                raise ValueError(f"Invalid fields for profile update: {bad}")
            for flag in ("channels_restricted", *FEATURE_FIELDS):
                if flag in fields:
                    fields[flag] = int(bool(fields[flag]))
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
        # profile_channels rows go with it (ON DELETE CASCADE).
        self.db.execute("DELETE FROM profiles WHERE id = ?", (profile_id,))
        self.db.commit()

    def _get_channel_ids(self, profile_id: int) -> builtins.list[int]:
        rows = self.db.execute(
            "SELECT channel_id FROM profile_channels WHERE profile_id = ?",
            (profile_id,),
        ).fetchall()
        return [r["channel_id"] for r in rows]

    def _set_channel_ids(self, profile_id: int, channel_ids: builtins.list[int]) -> None:
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
            pin_style=d.get("pin_style") or "digits",
            language=d.get("language") or "en",
            display_name=d.get("display_name", ""),
            avatar=d.get("avatar", "default"),
            theme=d.get("theme", "base"),
            search_mode=d.get("search_mode", "disabled"),
            created_at=d.get("created_at", ""),
            channels_restricted=bool(d.get("channels_restricted", 0)),
            auto_add_channel_id=d.get("auto_add_channel_id"),
            auto_add_mode=d.get("auto_add_mode") or "family",
            trusted_only=d.get("trusted_only") or "family",
            **self._features(d),
        )

    @staticmethod
    def _features(d: dict) -> dict[str, Any]:
        return {f: bool(d.get(f, f != "allow_wishlist")) for f in FEATURE_FIELDS}
