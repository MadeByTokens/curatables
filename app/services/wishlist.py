"""Kid wishlist: search YouTube through the server, ask a grown-up.

The kid's device never talks to YouTube: the server runs the search,
filters the results with the parent's rules, fetches the thumbnails
itself, and stores what the kid asks for. Nothing is downloaded until
a parent reviews and adds it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.backends.base import VideoBackend, VideoMetadata
from app.config import Config
from app.repositories.video_repo import VideoRepository
from app.repositories.wishlist_repo import WishlistRepository
from app.services.ids import make_video_id
from app.services.rate_limit import RateLimiter
from app.services.thumbnails import ThumbnailService

SEARCH_LIMITER = RateLimiter(max_events=10, window_seconds=300)
MAX_PAGES = 5
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")


class WishlistError(Exception):
    """A kid-facing reason the search or request could not go ahead."""


@dataclass
class WishlistResult:
    meta: VideoMetadata
    video_id: str
    status: str
    """'new', 'pending', 'approved' or 'in_library'."""


@dataclass
class WishlistPage:
    results: list[WishlistResult]
    page: int
    has_more: bool


def _contains_word(text: str, word: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.IGNORECASE) is not None


class WishlistService:
    def __init__(self, backend: VideoBackend, repo: WishlistRepository,
                 video_repo: VideoRepository, thumbnails: ThumbnailService,
                 config: Config):
        self.backend = backend
        self.repo = repo
        self.video_repo = video_repo
        self.thumbnails = thumbnails
        self.rules = config.wishlist

    def blocked_word_in(self, text: str) -> str | None:
        for word in self.rules.blocked_words:
            word = str(word).strip()
            if word and _contains_word(text, word):
                return word
        return None

    def passes(self, meta: VideoMetadata) -> bool:
        """The parent's rules for a single result."""
        if meta.is_live:
            return False
        if meta.extractor != "youtube" or not _YOUTUBE_ID.match(meta.video_id):
            return False
        limit = self.rules.max_duration_minutes
        if limit > 0 and meta.duration > limit * 60:
            return False
        if self.blocked_word_in(f"{meta.title}\n{meta.channel}"):
            return False
        allowed = [c.strip().lower() for c in self.rules.allowed_channels if c.strip()]
        if allowed and meta.channel.strip().lower() not in allowed:
            return False
        return True

    def search(self, profile_id: int, query: str, page: int = 1) -> WishlistPage:
        """One page of results that pass the rules. YouTube search has no
        page token, so page N asks for enough results to fill N pages and
        skips the ones before it."""
        query = " ".join(query.split())[:100]
        page = min(max(page, 1), MAX_PAGES)
        if not query:
            return WishlistPage([], page, False)
        if self.blocked_word_in(query):
            raise WishlistError("Let's look for something else.")
        if not SEARCH_LIMITER.check(profile_id):
            raise WishlistError("Too many searches. Wait a little, then try again.")
        per_page = self.rules.max_results
        found = self.backend.search(query, max_results=per_page * page * 2)
        asked = self.repo.statuses_for(profile_id)
        results: list[WishlistResult] = []
        for meta in found:
            if not self.passes(meta):
                continue
            video_id = make_video_id(meta.extractor, meta.video_id)
            status = asked.get(video_id, "new")
            if status == "rejected":
                continue
            if status == "new" and self.video_repo.get(video_id) is not None:
                status = "in_library"
            results.append(WishlistResult(meta, video_id, status))
            if len(results) > per_page * page:
                break
        has_more = len(results) > per_page * page and page < MAX_PAGES
        results = results[per_page * (page - 1):per_page * page]
        self.thumbnails.download_many([
            (r.video_id, r.meta.thumbnail_url or
             f"https://i.ytimg.com/vi/{r.meta.video_id}/hqdefault.jpg")
            for r in results
        ])
        return WishlistPage(results, page, has_more)

    def ask(self, profile_id: int, raw_id: str) -> str:
        """Record the kid's request after re-checking the video against the
        rules. Returns the composite video id."""
        if not _YOUTUBE_ID.match(raw_id):
            raise WishlistError("That didn’t work. Try again.")
        video_id = make_video_id("youtube", raw_id)
        if self.repo.statuses_for(profile_id).get(video_id) == "rejected":
            raise WishlistError("A grown-up said not this one.")
        meta = self.backend.fetch_video_info(f"https://www.youtube.com/watch?v={raw_id}")
        if meta is None or not self.passes(meta):
            raise WishlistError("You can't ask for this one. Try another video.")
        self.repo.add(profile_id, video_id, meta.extractor, meta.video_id, meta.title,
                      meta.channel, meta.duration,
                      meta.original_url or f"https://www.youtube.com/watch?v={raw_id}",
                      meta.thumbnail_url)
        self.thumbnails.download(video_id, meta.thumbnail_url or
                                 f"https://i.ytimg.com/vi/{raw_id}/hqdefault.jpg")
        return video_id
