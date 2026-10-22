"""The order a kid picked for a channel's videos, and for All videos.

Newest first is how every channel starts (a show's episodes in order,
then the newest downloaded: when it was added, or when its files were
last downloaded again after being freed). A kid can pick oldest first,
"came out first" (by YouTube's upload date, or when it was added for a
video with no such date), A to Z or Z to A by the titles that kid sees (their own
included, so "1 Trains", "2 Boats" put videos in order), or their own
order on a channel (My order). The choice is kept for each kid and
channel.

My order never changes by itself and never on a single tap: the kid
arranges a draft (kept on the box), starting from an order they choose
on purpose, with moves that each can be undone, and only Save makes it
their order; Undo save brings the one before back. A video added later
goes in among the others by when it came out, without moving any; one
the kid can no longer watch just drops out.
"""

from __future__ import annotations

import re

from app.models import Channel, Video, ViewerContext
from app.repositories.channel_sort_repo import ChannelSortRepository
from app.repositories.video_repo import fold_text
from app.services.content import ContentService, VideoNotFound
from app.services.kid_library import KidLibraryService

ALL_SORTS = ("newest", "oldest", "made", "az", "za")
SORTS = ALL_SORTS + ("mine",)
# The orders a draft of My order can start from.
START_SORTS = ALL_SORTS
UNDO_STEPS = 20
# Every video of a channel is read to order it; a family's channel holds
# far fewer.
_ALL = 100_000
_NUMBER = re.compile(r"(\d+)")


def release_key(video: Video) -> tuple:
    """When a video came out: YouTube's upload date (YYYYMMDD), else the
    day it was added; then when it was added, so same-day videos keep
    their order."""
    d = video.upload_date or ""
    day = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else (video.added_at or "")[:10]
    return (day, video.added_at or "", video.video_id)


def downloaded_key(video: Video) -> tuple:
    """When a video was last downloaded: again after its files were
    freed, else when it was added."""
    return (video.downloaded_at or video.added_at or "", video.video_id)


def title_key(title: str) -> list:
    """Sorts titles the way a person reads them: accents and capitals
    don't count, and numbers go by value ("2 Boats" before "10 Cars")."""
    return [int(part) if part.isdigit() else part
            for part in _NUMBER.split(fold_text(title).strip())]


def _episodes_first(videos: list[Video]) -> None:
    """A show's episodes in their order, ahead of the rest (a stable sort,
    so the rest keep the order they had)."""
    videos.sort(key=lambda v: (v.episode_key is None, v.episode_key or 0))


def with_new_ones(ids: list[str], videos: list[Video]) -> list[Video]:
    """`videos` in the order of `ids`. One missing from `ids` (added since)
    goes right after the last placed video that came out before it (first,
    if none did), so none of the placed ones move, not even one the kid
    moved to the top; one in `ids` the kid can't watch any more is left
    out."""
    by_id = {v.video_id: v for v in videos}
    placed = [by_id[i] for i in ids if i in by_id]
    known = set(ids)
    for new in sorted((v for v in videos if v.video_id not in known), key=release_key):
        key = release_key(new)
        earlier = [n for n, v in enumerate(placed) if release_key(v) <= key]
        placed.insert(earlier[-1] + 1 if earlier else 0, new)
    return placed


class ChannelOrderService:
    def __init__(self, repo: ChannelSortRepository, content: ContentService,
                 kid_lib: KidLibraryService):
        self.repo = repo
        self.content = content
        self.kid_lib = kid_lib

    # --- the chosen order ---

    def sort_for(self, viewer: ViewerContext, channel_id: int) -> str:
        if not viewer.is_child or not viewer.profile_id:
            return "newest"
        sort = self.repo.get(viewer.profile_id, channel_id)
        return sort if sort in SORTS else "newest"

    def choose(self, viewer: ViewerContext, channel_id: int, sort: str) -> None:
        """Picking an order only shows it; My order shows the saved one."""
        if viewer.profile_id and sort in SORTS:
            self.repo.set(viewer.profile_id, channel_id, None if sort == "newest" else sort)

    def sort_for_all(self, viewer: ViewerContext) -> str:
        if not viewer.is_child or not viewer.profile_id:
            return "newest"
        sort = self.repo.get_all(viewer.profile_id)
        return sort if sort in ALL_SORTS else "newest"

    def choose_all(self, viewer: ViewerContext, sort: str) -> None:
        if viewer.profile_id and sort in ALL_SORTS:
            self.repo.set_all(viewer.profile_id, None if sort == "newest" else sort)

    # --- listing ---

    def _ordered(self, viewer: ViewerContext, videos: list[Video], sort: str,
                 saved_first: list[Video] | None = None) -> list[Video]:
        """Every video in `sort` order. `saved_first`: videos a kid saved
        into their own channel, which newest first puts on top."""
        videos = list(videos)
        if sort == "newest":
            videos.sort(key=downloaded_key, reverse=True)
            _episodes_first(videos)
            return list(saved_first or []) + videos
        videos += saved_first or []
        if sort == "oldest":
            videos.sort(key=downloaded_key)
        elif sort == "made":
            videos.sort(key=release_key)
            _episodes_first(videos)
        else:
            videos = self.kid_lib.apply_overrides(viewer.kid_id, videos)
            videos.sort(key=lambda v: (title_key(v.title), v.video_id), reverse=sort == "za")
        return videos

    @staticmethod
    def _page(videos: list[Video], page: int, per_page: int) -> tuple[list[Video], int]:
        start = (page - 1) * per_page
        return videos[start:start + per_page], len(videos)

    def page_all(self, viewer: ViewerContext, page: int, per_page: int) -> tuple[list[Video], int]:
        """One page of every video the kid can watch, in the kid's order."""
        videos, _total = self.content.list_for_viewer(viewer, per_page=_ALL)
        return self._page(self._ordered(viewer, videos, self.sort_for_all(viewer)), page, per_page)

    def _in_channel(self, viewer: ViewerContext, channel: Channel) -> tuple[list[Video], list[Video]]:
        """The channel's videos the kid can watch, and the ones the kid
        saved into it (their own channel only)."""
        cid = channel.id or 0
        videos, _total = self.content.list_for_viewer(viewer, per_page=_ALL, channel_id=cid)
        saved = self._saved(viewer, cid, videos, _ALL) if self._owned(viewer, channel) else []
        return videos, saved

    def ordered(self, viewer: ViewerContext, channel: Channel, sort: str | None = None) -> list[Video]:
        """Every video of the channel in `sort` (default: the kid's)."""
        sort = sort or self.sort_for(viewer, channel.id or 0)
        videos, saved = self._in_channel(viewer, channel)
        if sort == "mine":
            mine = self.repo.my_order(viewer.kid_id, channel.id or 0)["saved"]
            if mine is None:
                return self._ordered(viewer, videos, "newest", saved)
            return with_new_ones(mine, videos + saved)
        return self._ordered(viewer, videos, sort, saved)

    def page(self, viewer: ViewerContext, channel: Channel, page: int,
             per_page: int) -> tuple[list[Video], int]:
        """One page of the channel in the kid's order."""
        if channel.id is None:
            return [], 0
        return self._page(self.ordered(viewer, channel), page, per_page)

    # --- My order ---

    def my_order(self, viewer: ViewerContext, channel_id: int) -> dict:
        return self.repo.my_order(viewer.kid_id, channel_id)

    def draft(self, viewer: ViewerContext, channel: Channel) -> list[Video]:
        """The draft being arranged; the first time, the saved order (or,
        with none saved, the order the kid was looking at)."""
        cid = channel.id or 0
        state = self.repo.my_order(viewer.kid_id, cid)
        if state["draft"] is None:
            start = self.sort_for(viewer, cid)
            shown = self.ordered(viewer, channel, start if start != "mine" or state["saved"] else "newest")
            state["draft"], state["undo"] = [v.video_id for v in shown], []
            self.repo.set_my_order(viewer.kid_id, cid, state)
            return shown
        videos, saved = self._in_channel(viewer, channel)
        return with_new_ones(state["draft"], videos + saved)

    def _change_draft(self, viewer: ViewerContext, channel: Channel, ids: list[str]) -> None:
        cid = channel.id or 0
        before = [v.video_id for v in self.draft(viewer, channel)]
        if ids == before:
            return  # the same tap again changes nothing
        state = self.repo.my_order(viewer.kid_id, cid)
        state["undo"] = (state["undo"] + [before])[-UNDO_STEPS:]
        state["draft"] = ids
        self.repo.set_my_order(viewer.kid_id, cid, state)

    def has_moves(self, viewer: ViewerContext, channel_id: int) -> bool:
        return bool(self.repo.my_order(viewer.kid_id, channel_id)["undo"])

    def start_from(self, viewer: ViewerContext, channel: Channel, sort: str) -> None:
        if sort in START_SORTS:
            ids = [v.video_id for v in self.ordered(viewer, channel, sort)]
            self._change_draft(viewer, channel, ids)

    def move(self, viewer: ViewerContext, channel: Channel, video_id: str, how: str,
             place: int = 0) -> int | None:
        """Move one video in the draft: up, down, top, end, or to `place`
        (counting from 1). Returns its new place, or None."""
        ids = [v.video_id for v in self.draft(viewer, channel)]
        if video_id not in ids:
            return None
        at = ids.index(video_id)
        to = {"up": at - 1, "down": at + 1, "top": 0, "end": len(ids) - 1,
              "place": place - 1}.get(how)
        if to is None:
            return None
        to = min(max(to, 0), len(ids) - 1)
        ids.insert(to, ids.pop(at))
        self._change_draft(viewer, channel, ids)
        return to + 1

    def undo(self, viewer: ViewerContext, channel: Channel) -> None:
        cid = channel.id or 0
        state = self.repo.my_order(viewer.kid_id, cid)
        if state["undo"]:
            state["draft"] = state["undo"].pop()
            self.repo.set_my_order(viewer.kid_id, cid, state)

    def save(self, viewer: ViewerContext, channel: Channel) -> None:
        cid = channel.id or 0
        ids = [v.video_id for v in self.draft(viewer, channel)]
        state = self.repo.my_order(viewer.kid_id, cid)
        state.update(saved_before=state["saved"], sort_before=self.sort_for(viewer, cid),
                     saved=ids, draft=None, undo=[])
        self.repo.set_my_order(viewer.kid_id, cid, state)
        self.choose(viewer, cid, "mine")

    def cancel(self, viewer: ViewerContext, channel: Channel) -> None:
        cid = channel.id or 0
        state = self.repo.my_order(viewer.kid_id, cid)
        state.update(draft=None, undo=[])
        self.repo.set_my_order(viewer.kid_id, cid, state)

    def can_undo_save(self, viewer: ViewerContext, channel_id: int) -> bool:
        state = self.repo.my_order(viewer.kid_id, channel_id)
        return state["saved"] is not None and state["sort_before"] is not None

    def undo_save(self, viewer: ViewerContext, channel: Channel) -> None:
        """Back to the order before the last save, and the choice then."""
        cid = channel.id or 0
        state = self.repo.my_order(viewer.kid_id, cid)
        if state["sort_before"] is None:
            return
        sort_before = state["sort_before"]
        state.update(saved=state["saved_before"], saved_before=None, sort_before=None)
        self.repo.set_my_order(viewer.kid_id, cid, state)
        self.choose(viewer, cid, sort_before)

    @staticmethod
    def _owned(viewer: ViewerContext, channel: Channel) -> bool:
        return viewer.is_child and channel.owner_profile_id == viewer.profile_id

    def _saved(self, viewer: ViewerContext, channel_id: int, present: list[Video],
               limit: int) -> list[Video]:
        """Videos the kid saved into their own channel (from Customize)
        that don't live in it. Ones the kid can no longer see are
        skipped."""
        ids, _total = self.kid_lib.channel_video_ids(viewer.kid_id, channel_id, per_page=limit)
        here = {v.video_id for v in present}
        saved = []
        for vid in ids:
            if vid in here:
                continue
            try:
                saved.append(self.content.get_video_for_viewer(vid, viewer))
            except VideoNotFound:
                continue
        return saved
