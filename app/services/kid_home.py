"""A kid's Home, arranged like a tablet's home screen.

Home is a grid of SLOTS spots. Until the kid changes anything it shows
their newest videos; the first change saves that layout and from then on
the kid decides what sits where, gaps included. A video the kid can no
longer watch (deleted, moved out of their channels, hidden) leaves its
spot empty.
"""

from __future__ import annotations

from app.models import Video, ViewerContext
from app.repositories.home_slot_repo import HomeSlotRepository
from app.services.content import ContentService

SLOTS = 24
NEWEST_ON_A_NEW_HOME = 12


class KidHomeService:

    def __init__(self, repo: HomeSlotRepository, content: ContentService):
        self.repo = repo
        self.content = content

    def _watchable(self, video_id: str, viewer: ViewerContext) -> Video | None:
        """The video if this kid may open it from a list (a freed one
        downloads again when opened)."""
        video = self.content.video_repo.get(video_id)
        if (video is None or video.status != "active"
                or video.download_status not in ("ready", "evicted")
                or not self.content.can_view(video, viewer)
                or self.content.hidden_by_viewer(video, viewer)):
            return None
        return video

    def _slots(self, viewer: ViewerContext) -> dict[int, str]:
        if self.repo.is_customized(viewer.kid_id):
            return self.repo.slots(viewer.kid_id)
        newest, _total = self.content.list_for_viewer(viewer, per_page=NEWEST_ON_A_NEW_HOME)
        return {i: v.video_id for i, v in enumerate(newest)}

    def layout(self, viewer: ViewerContext) -> list[Video | None]:
        """SLOTS entries, None for an empty spot."""
        grid: list[Video | None] = [None] * SLOTS
        for pos, vid in self._slots(viewer).items():
            if 0 <= pos < SLOTS:
                grid[pos] = self._watchable(vid, viewer)
        return grid

    def customized(self, viewer: ViewerContext) -> bool:
        return self.repo.is_customized(viewer.kid_id)

    def contains(self, viewer: ViewerContext, video_id: str) -> bool:
        return video_id in self._slots(viewer).values()

    def _live_slots(self, viewer: ViewerContext) -> dict[int, str]:
        """The saved layout without the videos the kid can't open any
        more, so their spots count as free."""
        return {pos: v.video_id for pos, v in enumerate(self.layout(viewer)) if v is not None}

    def add(self, viewer: ViewerContext, video_id: str, position: int | None = None) -> bool:
        """Put a video on Home: in `position` (whatever was there leaves
        Home), or in the first free spot. False when it can't be watched
        or Home is full."""
        if self._watchable(video_id, viewer) is None:
            return False
        slots = {p: v for p, v in self._live_slots(viewer).items() if v != video_id}
        if position is None:
            position = next((p for p in range(SLOTS) if p not in slots), None)
            if position is None:
                return False
        elif not 0 <= position < SLOTS:
            return False
        slots[position] = video_id
        self.repo.save(viewer.kid_id, slots)
        return True

    def remove(self, viewer: ViewerContext, video_id: str) -> None:
        slots = {p: v for p, v in self._live_slots(viewer).items() if v != video_id}
        self.repo.save(viewer.kid_id, slots)

    def move(self, viewer: ViewerContext, source: int, target: int) -> None:
        """Move the video in `source` to `target`; one already there
        swaps places with it."""
        if not (0 <= source < SLOTS and 0 <= target < SLOTS):
            return
        slots = self._live_slots(viewer)
        if source not in slots:
            return
        moving = slots.pop(source)
        if target in slots:
            slots[source] = slots[target]
        slots[target] = moving
        self.repo.save(viewer.kid_id, slots)
