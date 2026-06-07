from __future__ import annotations
"""Event service — usage logging and statistics."""

import json

from app.models import Event
from app.repositories import EventRepository
from app.services.metrics import MetricsService


class EventService:
    def __init__(self, event_repo: EventRepository,
                 metrics: MetricsService | None = None):
        self.event_repo = event_repo
        # Default to a disabled MetricsService so unit tests that wire
        # the service directly (without going through FastAPI DI) can
        # call .log() without a metrics arg.
        self.metrics = metrics or MetricsService(enabled=False)

    def log(self, event_type: str, video_id: str | None = None,
            profile_id: int | None = None, data: dict | None = None) -> None:
        event = Event(
            event_type=event_type,
            video_id=video_id,
            profile_id=profile_id,
            data_json=json.dumps(data or {}),
        )
        self.event_repo.insert(event)
        if event_type == "play":
            self.metrics.record_kid_play()

    def get_watch_time_today(self, profile_id: int | None = None) -> int:
        """Returns total watch seconds for today."""
        return self.event_repo.get_watch_time_today(profile_id)

    def list_recent(self, limit: int = 50,
                    profile_id: int | None = None) -> list[Event]:
        return self.event_repo.list_recent(limit, profile_id)
