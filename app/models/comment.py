from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class Comment:
    video_id: str = ""
    body: str = ""
    profile_id: int | None = None
    parent_comment_id: int | None = None
    is_parent_user: int = 0
    created_at: str = ""
    id: int | None = None
    # Joined fields (not always present)
    author_name: str = ""
    author_avatar: str = "default"
    replies: list = field(default_factory=list)
