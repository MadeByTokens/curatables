"""The order each kid picked for a channel's videos, and for All videos."""

from __future__ import annotations

import json

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

    # --- My order ---

    _ORDER_FIELDS = ("saved", "saved_before", "sort_before", "draft", "undo")

    def my_order(self, profile_id: int, channel_id: int) -> dict:
        """saved, saved_before, draft (lists of video ids or None),
        sort_before (str or None) and undo (a list of earlier drafts)."""
        row = self.db.execute(
            "SELECT saved, saved_before, sort_before, draft, undo FROM profile_channel_order "
            "WHERE profile_id = ? AND channel_id = ?", (profile_id, channel_id)).fetchone()
        if row is None:
            return {"saved": None, "saved_before": None, "sort_before": None, "draft": None, "undo": []}
        out = {k: (json.loads(row[k]) if row[k] else None) for k in ("saved", "saved_before", "draft")}
        out["sort_before"] = row["sort_before"]
        out["undo"] = json.loads(row["undo"] or "[]")
        return out

    def set_my_order(self, profile_id: int, channel_id: int, order: dict) -> None:
        def text(v):
            return None if v is None else json.dumps(v)
        with self.db:
            self.db.execute(
                "INSERT INTO profile_channel_order (profile_id, channel_id, saved, saved_before, "
                "sort_before, draft, undo) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT (profile_id, channel_id) DO UPDATE SET saved = excluded.saved, "
                "saved_before = excluded.saved_before, sort_before = excluded.sort_before, "
                "draft = excluded.draft, undo = excluded.undo",
                (profile_id, channel_id, text(order["saved"]), text(order["saved_before"]),
                 order["sort_before"], text(order["draft"]), json.dumps(order["undo"])))
