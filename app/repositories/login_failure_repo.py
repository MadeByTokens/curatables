"""Failed parent login attempts (client address + unix time), and wrong
kid PINs per profile (client "pin:<profile id>")."""
from __future__ import annotations

from app.repositories.base import BaseRepository


class LoginFailureRepository(BaseRepository):

    def record(self, client: str, at: float) -> None:
        self.db.execute(
            "INSERT INTO login_failures (client, at) VALUES (?, ?)", (client, at))
        self.db.execute("DELETE FROM login_failures WHERE at < ?", (at - 86400,))
        self.db.commit()

    def count_since(self, since: float, client: str | None = None) -> int:
        if client is None:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM login_failures WHERE at >= ? AND client NOT LIKE 'pin:%'",
                (since,)).fetchone()
        else:
            row = self.db.execute(
                "SELECT COUNT(*) AS n FROM login_failures WHERE at >= ? AND client = ?",
                (since, client)).fetchone()
        return int(row["n"])

    def clear(self, client: str) -> None:
        self.db.execute("DELETE FROM login_failures WHERE client = ?", (client,))
        self.db.commit()
