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
    # Filled by joins in some queries only.
    author_name: str = ""
    video_title: str = ""
    replies: list = field(default_factory=list)
