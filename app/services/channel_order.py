"""The order a kid picked for a channel's videos, and for All videos.

Newest first is how every channel starts (a show's episodes in order).
A kid can pick oldest first, or A to Z or Z to A by the titles that kid
sees, their own included, so "1 Trains", "2 Boats" put videos in the
order the kid wants. The choice is kept for each kid and channel.
"""

from __future__ import annotations

import re

from app.models import Channel, Video, ViewerContext
from app.repositories.channel_sort_repo import ChannelSortRepository
from app.repositories.video_repo import fold_text
from app.services.content import ContentService, VideoNotFound
from app.services.kid_library import KidLibraryService

SORTS = ("newest", "oldest", "az", "za")
# Every video of a channel is read to sort it by title; a family's
# channel holds far fewer.
_ALL = 100_000
_NUMBER = re.compile(r"(\d+)")


def title_key(title: str) -> list:
    """Sorts titles the way a person reads them: accents and capitals
    don't count, and numbers go by value ("2 Boats" before "10 Cars")."""
    return [int(part) if part.isdigit() else part
            for part in _NUMBER.split(fold_text(title).strip())]


class ChannelOrderService:
    def __init__(self, repo: ChannelSortRepository, content: ContentService,
                 kid_lib: KidLibraryService):
        self.repo = repo
        self.content = content
        self.kid_lib = kid_lib

    def sort_for(self, viewer: ViewerContext, channel_id: int) -> str:
        if not viewer.is_child or not viewer.profile_id:
            return "newest"
        sort = self.repo.get(viewer.profile_id, channel_id)
        return sort if sort in SORTS else "newest"

    def choose(self, viewer: ViewerContext, channel_id: int, sort: str) -> None:
        if viewer.profile_id and sort in SORTS:
            self.repo.set(viewer.profile_id, channel_id, None if sort == "newest" else sort)

    def sort_for_all(self, viewer: ViewerContext) -> str:
        if not viewer.is_child or not viewer.profile_id:
            return "newest"
        sort = self.repo.get_all(viewer.profile_id)
        return sort if sort in SORTS else "newest"

    def choose_all(self, viewer: ViewerContext, sort: str) -> None:
        if viewer.profile_id and sort in SORTS:
            self.repo.set_all(viewer.profile_id, None if sort == "newest" else sort)

    def page_all(self, viewer: ViewerContext, page: int, per_page: int) -> tuple[list[Video], int]:
        """One page of every video the kid can watch, in the kid's order."""
        sort = self.sort_for_all(viewer)
        if sort == "newest":
            return self.content.list_for_viewer(viewer, page=page, per_page=per_page)
        videos, _total = self.content.list_for_viewer(viewer, per_page=_ALL)
        return self._sorted(viewer, videos, sort, page, per_page)

    def _sorted(self, viewer: ViewerContext, videos: list[Video], sort: str, page: int,
                per_page: int) -> tuple[list[Video], int]:
        if sort == "oldest":
            videos.sort(key=lambda v: (v.added_at or "", v.video_id))
        else:
            videos = self.kid_lib.apply_overrides(viewer.kid_id, videos)
            videos.sort(key=lambda v: (title_key(v.title), v.video_id), reverse=sort == "za")
        start = (page - 1) * per_page
        return videos[start:start + per_page], len(videos)

    def page(self, viewer: ViewerContext, channel: Channel, page: int,
             per_page: int) -> tuple[list[Video], int]:
        """One page of the channel in the kid's order, with the videos a
        kid saved into their own channel among them."""
        if channel.id is None:
            return [], 0
        cid = channel.id
        sort = self.sort_for(viewer, cid)
        if sort == "newest":
            videos, total = self.content.list_for_viewer(
                viewer, page=page, per_page=per_page, channel_id=cid)
            if self._owned(viewer, channel) and page == 1:
                videos = self._saved(viewer, cid, videos, per_page) + videos
            return videos, total
        videos, _total = self.content.list_for_viewer(viewer, per_page=_ALL, channel_id=cid)
        if self._owned(viewer, channel):
            videos = videos + self._saved(viewer, cid, videos, _ALL)
        return self._sorted(viewer, videos, sort, page, per_page)

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
