from __future__ import annotations
"""Profile service — child profile management."""

import re
import unicodedata

from app.models import Profile
from app.repositories import ProfileRepository


def slugify(text: str) -> str:
    """Derive a short ascii slug from a human name.

    Falls back to "kid" if the result would be empty after stripping.
    """
    text = unicodedata.normalize("NFKD", text or "")
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text or "kid"


class ProfileService:
    def __init__(self, profile_repo: ProfileRepository):
        self.profile_repo = profile_repo

    def list(self) -> list[Profile]:
        return self.profile_repo.list()

    def get(self, profile_id: int) -> Profile | None:
        return self.profile_repo.get(profile_id)

    def unique_slug(self, base: str) -> str:
        """Return `base` (or `base-2`, `base-3`, ...) so it doesn't collide."""
        base = slugify(base)
        existing = {p.name for p in self.profile_repo.list()}
        if base not in existing:
            return base
        n = 2
        while f"{base}-{n}" in existing:
            n += 1
        return f"{base}-{n}"

    def create(self, name: str, display_name: str = "", pin: str = "",
               avatar: str = "default", theme: str = "base",
               search_mode: str = "disabled",
               allowed_channel_ids: list[int] | None = None) -> int:
        profile = Profile(
            name=name, display_name=display_name, pin=pin,
            avatar=avatar, theme=theme, search_mode=search_mode,
            allowed_channel_ids=allowed_channel_ids or [],
        )
        return self.profile_repo.create(profile)

    def update(self, profile_id: int, **fields) -> None:
        self.profile_repo.update(profile_id, **fields)

    def delete(self, profile_id: int) -> None:
        self.profile_repo.delete(profile_id)
