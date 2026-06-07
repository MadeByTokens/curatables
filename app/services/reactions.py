from __future__ import annotations
"""Reaction service — emoji reactions on videos."""

import json
from app.repositories.reaction_repo import ReactionRepository
from app.repositories.event_repo import EventRepository


EMOJI_MAP = [
    ("love", "\u2764\ufe0f"),
    ("funny", "\U0001f602"),
    ("cool", "\U0001f60e"),
    ("wow", "\U0001f62e"),
    ("learned", "\U0001f4a1"),
    ("boring", "\U0001f634"),
]


class ReactionService:
    def __init__(self, reaction_repo: ReactionRepository,
                 event_repo: EventRepository):
        self.reaction_repo = reaction_repo
        self.event_repo = event_repo

    def react(self, profile_id: int, video_id: str, emoji: str) -> None:
        allowed = [name for name, _ in EMOJI_MAP]
        if emoji not in allowed:
            return
        self.reaction_repo.upsert(profile_id, video_id, emoji)
        self.event_repo.insert_raw(
            "video_reaction", video_id, profile_id,
            json.dumps({"emoji": emoji}),
        )

    def remove(self, profile_id: int, video_id: str) -> None:
        self.reaction_repo.delete(profile_id, video_id)

    def get_for_video(self, profile_id: int, video_id: str) -> str | None:
        return self.reaction_repo.get(profile_id, video_id)

    def get_counts(self, video_id: str) -> dict[str, int]:
        return self.reaction_repo.counts_for_video(video_id)

    def get_bulk_counts(self, video_ids: list[str]) -> dict[str, dict[str, int]]:
        return self.reaction_repo.counts_for_videos(video_ids)

    def get_emoji_list(self) -> list[tuple[str, str]]:
        """Return list of (name, unicode_char) for display."""
        return list(EMOJI_MAP)
