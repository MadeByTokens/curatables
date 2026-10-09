from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ViewerContext:
    viewer_type: str                            # "parent" | "child" | "anonymous"
    profile_id: int | None = None
    profile_name: str = ""
    display_name: str = ""                        # shown in kid UI header
    allowed_channel_ids: list[int] | None = None  # None = unrestricted profile
    search_mode: str = "disabled"
    theme: str = "base"
    has_multiple_profiles: bool = False  # drives "Switch profile" affordance visibility
    allow_comments: bool = True
    allow_reactions: bool = True
    allow_tags: bool = True
    allow_customize: bool = True
    allow_upload: bool = True
    auto_subtitles: bool = True

    @property
    def kid_header_name(self) -> str:
        """Name to show in the kid UI header."""
        return self.display_name or self.profile_name or "curatables"

    @property
    def kid_id(self) -> int:
        """The child's profile id; only for routes that require a child."""
        if self.profile_id is None:
            raise LookupError("viewer is not a child")
        return self.profile_id

    @property
    def is_parent(self) -> bool:
        return self.viewer_type == "parent"

    @property
    def is_child(self) -> bool:
        return self.viewer_type == "child"
