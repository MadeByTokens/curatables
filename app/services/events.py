"""Event service — usage event logging."""

from __future__ import annotations

import json

from app.models import Event
from app.repositories import EventRepository
from app.services.metrics import MetricsService


class EventService:
    def __init__(self, event_repo: EventRepository,
                 metrics: MetricsService | None = None):
        self.event_repo = event_repo
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
        if event_type == "video_play":
            self.metrics.record_kid_play()
