from __future__ import annotations
from dataclasses import dataclass, field


@dataclass
class ViewerContext:
    viewer_type: str                            # "parent" | "child" | "anonymous"
    profile_id: int | None = None
    profile_name: str = ""
    display_name: str = ""                        # shown in kid UI header
    allowed_channel_ids: list[int] | None = None  # None = all channels visible
    search_mode: str = "disabled"
    theme: str = "base"
    has_multiple_profiles: bool = False  # drives "Switch profile" affordance visibility

    @property
    def kid_header_name(self) -> str:
        """Name to show in the kid UI header."""
        return self.display_name or self.profile_name or "curatables"

    @property
    def is_parent(self) -> bool:
        return self.viewer_type == "parent"

    @property
    def is_child(self) -> bool:
        return self.viewer_type == "child"

    def can_see_channel(self, channel_id: int | None) -> bool:
        """Check if this viewer can see content from a given channel."""
        if self.allowed_channel_ids is None:
            return True  # unrestricted profile sees everything
        if channel_id is None:
            return False  # restricted profile cannot see uncategorized videos
        return channel_id in self.allowed_channel_ids
