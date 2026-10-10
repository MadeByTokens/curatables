from dataclasses import dataclass


@dataclass
class Event:
    event_type: str
    video_id: str | None = None
    profile_id: int | None = None
    data_json: str = "{}"
    timestamp: str = ""
    id: int | None = None
    # Filled by joins in some queries only.
