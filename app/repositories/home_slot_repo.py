"""A kid's Home: which video sits in which spot."""

from __future__ import annotations

from app.repositories.base import BaseRepository


class HomeSlotRepository(BaseRepository):

    def is_customized(self, profile_id: int) -> bool:
        row = self.db.execute(
            "SELECT home_customized FROM profiles WHERE id = ?", (profile_id,)).fetchone()
        return bool(row and row["home_customized"])

    def slots(self, profile_id: int) -> dict[int, str]:
        rows = self.db.execute(
            "SELECT position, video_id FROM profile_home_slots WHERE profile_id = ?",
            (profile_id,)).fetchall()
        return {r["position"]: r["video_id"] for r in rows}

    def save(self, profile_id: int, slots: dict[int, str]) -> None:
        """Replace the kid's whole layout in one transaction, and mark
        Home as arranged by the kid."""
        with self.db:
            self.db.execute("DELETE FROM profile_home_slots WHERE profile_id = ?", (profile_id,))
            self.db.executemany(
                "INSERT INTO profile_home_slots (profile_id, position, video_id) VALUES (?, ?, ?)",
                [(profile_id, pos, vid) for pos, vid in sorted(slots.items())])
            self.db.execute("UPDATE profiles SET home_customized = 1 WHERE id = ?", (profile_id,))
