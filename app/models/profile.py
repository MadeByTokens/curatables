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
    pin_style: str = "digits"       # "digits" | "pictures"
    language: str = "en"            # app.i18n.LANGUAGE_CODES
    created_at: str = ""
    id: int | None = None
    allowed_channel_ids: list[int] = field(default_factory=list)
    channels_restricted: bool = False
    allow_comments: bool = True
    allow_reactions: bool = True
    allow_tags: bool = True
    allow_customize: bool = True
    allow_upload: bool = True
    auto_subtitles: bool = True


FEATURE_SWITCHES = (
    ("allow_reactions", "Emoji reactions", True),
    ("allow_comments", "Comments and replies", False),
    ("allow_tags", "Tags (typing words onto videos)", False),
    ("allow_customize", "Customize videos and own channels (titles, pictures)", False),
    ("allow_upload", "Upload videos from this device", False),
    ("auto_subtitles", "Turn subtitles on automatically", False),
)
"""(field, label, on-by-default-for-a-new-profile) for the parent form."""
