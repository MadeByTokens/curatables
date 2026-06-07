from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Profile:
    name: str
    pin: str = ""
    display_name: str = ""     # shown in kid UI header; falls back to name if empty
    avatar: str = "default"
    theme: str = "base"
    search_mode: str = "disabled"   # "disabled" | "curated" | "open"
    created_at: str = ""
    id: int | None = None
    allowed_channel_ids: list[int] = field(default_factory=list)
