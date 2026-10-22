"""Kid wishlist: search YouTube through the server, ask a grown-up.

The kid's device never talks to YouTube: the server runs the search,
filters the results with the parent's rules, fetches the thumbnails
itself, and stores what the kid asks for. Nothing is downloaded until
a parent reviews and adds it.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field, replace
from typing import Callable

from app.backends.base import BackendErrorType, ChannelHit, VideoBackend, VideoMetadata
from app.config import Config
from app.models import Profile
from app.repositories.profile_repo import ProfileRepository
from app.repositories.trusted_channel_repo import TrustedChannelRepository
from app.repositories.video_repo import VideoRepository, fold_text
from app.repositories.wishlist_repo import WishlistRepository
from app.services.family_time import since_days_ago
from app.services.ids import make_video_id
from app.services.search_gate import GATE, SearchRefused
from app.services.thumbnails import ThumbnailService
from app.spelling import near_blocked

logger = logging.getLogger(__name__)

MAX_PAGES = 5
MAX_FETCH = 100
_YOUTUBE_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
SORTS = {"videos": ("best", "newest", "az", "za"),
         "channels": ("best", "az", "za"),
         "channel": ("newest", "az", "za")}

KID_MESSAGES = {
    "rules": "Let's look for something else.",
    "slow": "Whoa, so many taps! Let's slow down a little, then try again.",
    "busy": "Lots of searching right now. Try again in a minute.",
    "resting": "Searching is taking a nap. Try again later.",
    "broken": "Searching isn't working right now. Try again later.",
    "gone": "That channel can't be shown. Try another one.",
    "failed": "That didn’t work. Try again.",
    "rejected": "A grown-up said not this one.",
    "not_allowed": "You can't ask for this one. Try another video.",
}


class WishlistError(Exception):
    """A kid-facing reason the search or request could not go ahead."""

    def __init__(self, kind: str):
        super().__init__(KID_MESSAGES[kind])
        self.kind = kind


@dataclass
class KidRules:
    """One kid's trusted YouTube channel IDs, and whether Find new shows
    only those."""
    trusted: set[str] = field(default_factory=set)
    only_trusted: bool = False

    def allows_channel(self, channel_id: str) -> bool:
        return not self.only_trusted or channel_id in self.trusted


@dataclass
class WishlistResult:
    meta: VideoMetadata
    video_id: str
    status: str
    """'new', 'pending', 'approved' or 'in_library'."""


@dataclass
class WishlistChannel:
    hit: ChannelHit
    thumb_id: str


@dataclass
class WishlistPage:
    results: list[WishlistResult] = field(default_factory=list)
    channels: list[WishlistChannel] = field(default_factory=list)
    page: int = 1
    has_more: bool = False
    title: str = ""


def _contains_word(text: str, word: str) -> bool:
    return re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.IGNORECASE) is not None


def blocked_word_in(words: list, text: str, near: bool = False) -> str | None:
    """The first of the parent's blocked words found in text, if any.
    With `near`, a long blocked word spelled a little differently counts
    too ("skibidy" for "skibidi"; see app.spelling)."""
    folded = re.findall(r"\w+", fold_text(text)) if near else []
    for word in words:
        word = str(word).strip()
        if word and _contains_word(text, word):
            return word
        if word and folded and any(near_blocked(w, fold_text(word)) for w in folded):
            return word
    return None


_CANNOT_ADD = (BackendErrorType.VIDEO_UNAVAILABLE, BackendErrorType.AGE_RESTRICTED)


class WishlistService:
    def __init__(self, backend: VideoBackend, repo: WishlistRepository,
                 video_repo: VideoRepository, thumbnails: ThumbnailService,
                 config: Config):
        self.backend = backend
        self.repo = repo
        self.video_repo = video_repo
        self.thumbnails = thumbnails
        self.rules = config.wishlist
        self.timezone = config.server.timezone
        self.trusted = TrustedChannelRepository(repo.db)
        self.profiles = ProfileRepository(repo.db)

    def blocked_word_in(self, text: str, near: bool = False) -> str | None:
        return blocked_word_in(self.rules.blocked_words, text, near)

    def kid_rules(self, profile_id: int) -> KidRules:
        """The kid's trusted channels; "only trusted channels" is theirs
        if set, else the family's."""
        profile = self.profiles.get(profile_id)
        mode = profile.trusted_only if profile else "family"
        only = mode == "on" if mode in ("on", "off") else self.rules.only_trusted_channels
        return KidRules(self.trusted.ids_for(profile_id), bool(only))

    def auto_add_into(self, profile: Profile | None) -> int | None:
        """The channel this kid's requests from trusted channels go
        straight into (theirs if set, else the family's), or None. The
        daily allowance is checked when adding (auto_adds_left)."""
        if profile is None or profile.auto_add_mode == "off":
            return None
        if profile.auto_add_mode == "on":
            return profile.auto_add_channel_id
        return self.rules.auto_add_channel_id

    def auto_add_target(self, profile: Profile | None, picked: int | None) -> int | None:
        """Where a request from a trusted channel goes straight in: the
        parent's channel the kid picked when asking (`picked`, if the
        family allows it), else auto_add_into. A kid set to wait waits."""
        if profile is None or profile.auto_add_mode == "off":
            return None
        if self.rules.auto_add_kid_choice and picked is not None:
            return picked
        return self.auto_add_into(profile)

    def auto_adds_left(self, profile_id: int) -> int:
        """How many more requests may go straight in for this kid today
        (the family's day, from its time zone)."""
        used = self.repo.auto_added_since(profile_id, since_days_ago(0, self.timezone))
        return max(self.rules.auto_add_per_day - used, 0)

    def passes(self, meta: VideoMetadata, kid: KidRules | None = None) -> bool:
        """The parent's rules for a single result, and the kid's trusted
        channels when they may see only those."""
        if meta.is_live:
            return False
        if meta.extractor != "youtube" or not _YOUTUBE_ID.match(meta.video_id):
            return False
        limit = self.rules.max_duration_minutes
        if limit > 0 and meta.duration > limit * 60:
            return False
        # A title or channel name can be spelled to slip past a filter; a
        # description is long, so only the exact words count there.
        if (self.blocked_word_in(f"{meta.title}\n{meta.channel}", near=True)
                or self.blocked_word_in(meta.description)):
            return False
        return kid is None or kid.allows_channel(meta.channel_id)

    def may_skip_review(self, meta: VideoMetadata, kid: KidRules) -> bool:
        """Whether a request may be added without the parent watching it:
        it passes the rules and its YouTube channel, by ID, is one of the
        kid's trusted channels."""
        return bool(meta.channel_id) and meta.channel_id in kid.trusted and self.passes(meta, kid)

    def channel_allowed(self, name: str) -> bool:
        return not self.blocked_word_in(name, near=True)

    def _fetch(self, profile_id: int, key: tuple, want: int, call):
        def run(n):
            items = call(n)
            return items, self.backend.last_error
        try:
            return GATE.fetch(key, want, profile_id, run)
        except SearchRefused as e:
            raise WishlistError(e.kind) from None

    def _want(self, page: int, factor: int = 2) -> int:
        return min(self.rules.max_results * page * factor, MAX_FETCH)

    def _paged(self, items: list, page: int, sort: str, name) -> tuple[list, bool]:
        per_page = self.rules.max_results
        has_more = len(items) > per_page * page and page < MAX_PAGES
        shown = items[per_page * (page - 1):per_page * page]
        if sort in ("az", "za"):
            shown.sort(key=lambda x: name(x).casefold(), reverse=sort == "za")
        return shown, has_more

    def _videos(self, profile_id: int, kid: KidRules, found: list[VideoMetadata], page: int,
                sort: str) -> tuple[list[WishlistResult], bool]:
        asked = self.repo.statuses_for(profile_id)
        results: list[WishlistResult] = []
        for meta in found:
            if not self.passes(meta, kid):
                continue
            video_id = make_video_id(meta.extractor, meta.video_id)
            status = asked.get(video_id, "new")
            if status in ("rejected", "unavailable"):
                continue
            if status == "new" and self.video_repo.get(video_id) is not None:
                status = "in_library"
            results.append(WishlistResult(meta, video_id, status))
        shown, has_more = self._paged(results, page, sort, lambda r: r.meta.title)
        self.thumbnails.download_many([(r.video_id, r.meta.thumbnail_url) for r in shown])
        GATE.remember_shown([r.meta for r in shown])
        return shown, has_more

    def search(self, profile_id: int, query: str, page: int = 1,
               mode: str = "videos", sort: str = "best") -> WishlistPage:
        """One page of results that pass the rules. YouTube search has no
        page token, so page N asks for enough results to fill N pages and
        skips the ones before it."""
        query = " ".join(query.split())[:100]
        page = min(max(page, 1), MAX_PAGES)
        mode = mode if mode in ("videos", "channels") else "videos"
        sort = sort if sort in SORTS[mode] else SORTS[mode][0]
        if not query:
            return WishlistPage(page=page)
        if self.blocked_word_in(query, near=True):
            raise WishlistError("rules")
        kid = self.kid_rules(profile_id)
        try:
            GATE.tap(profile_id)
        except SearchRefused as e:
            raise WishlistError(e.kind) from None
        want = self._want(page)
        if mode == "channels":
            hits = self._fetch(profile_id, ("channels", query.casefold()), want,
                               lambda n: self.backend.search_channels(query, max_results=n))
            ok = [WishlistChannel(h, f"ytchannel_{h.channel_id}") for h in hits
                  if CHANNEL_ID.match(h.channel_id) and self.channel_allowed(h.name)
                  and kid.allows_channel(h.channel_id)]
            shown, has_more = self._paged(ok, page, sort, lambda c: c.hit.name)
            self.thumbnails.download_many([(c.thumb_id, c.hit.thumbnail_url) for c in shown])
            return WishlistPage(channels=shown, page=page, has_more=has_more)
        newest = sort == "newest"
        found = self._fetch(profile_id, ("videos", query.casefold(), newest), want,
                            lambda n: self.backend.search(query, max_results=n, newest_first=newest))
        results, has_more = self._videos(profile_id, kid, found, page, sort)
        return WishlistPage(results=results, page=page, has_more=has_more)

    def channel(self, profile_id: int, channel_id: str, page: int = 1,
                sort: str = "newest") -> WishlistPage:
        """A channel's latest videos, through the same rules."""
        kid = self.kid_rules(profile_id)
        if not CHANNEL_ID.match(channel_id) or not kid.allows_channel(channel_id):
            raise WishlistError("gone")
        page = min(max(page, 1), MAX_PAGES)
        sort = sort if sort in SORTS["channel"] else "newest"
        try:
            GATE.tap(profile_id)
        except SearchRefused as e:
            raise WishlistError(e.kind) from None
        url = f"https://www.youtube.com/channel/{channel_id}/videos"
        # A channel listing is cheap, and the rules can drop many of its
        # videos (long compilations, say), so fetch more per page than a
        # search does.
        want = self._want(page, factor=4)
        found = self._fetch(profile_id, ("channel", channel_id), want,
                            lambda n: self.backend.fetch_channel_videos(url, max_results=n)[1])
        # A channel listing names the channel once; every entry carries
        # its name, and here its ID too.
        found = [m if m.channel_id else replace(m, channel_id=channel_id) for m in found]
        name = found[0].channel if found else ""
        if not name or not self.channel_allowed(name):
            raise WishlistError("gone")
        results, has_more = self._videos(profile_id, kid, found, page, sort)
        # A full listing means the channel has more than was fetched.
        has_more = has_more or (len(found) >= want and want < MAX_FETCH and page < MAX_PAGES)
        return WishlistPage(results=results, page=page, has_more=has_more, title=name)

    def ask(self, profile_id: int, raw_id: str) -> tuple[int, bool]:
        """Record the kid's request after re-checking the video against the
        rules. Returns the request id, and whether YouTube's full details
        still have to be fetched (see fill_details): a video the kid was
        just shown is recorded from its search result, so the kid doesn't
        wait for YouTube."""
        if not _YOUTUBE_ID.match(raw_id):
            raise WishlistError("failed")
        video_id = make_video_id("youtube", raw_id)
        if self.repo.statuses_for(profile_id).get(video_id) == "rejected":
            raise WishlistError("rejected")
        url = f"https://www.youtube.com/watch?v={raw_id}"
        known = self.repo.latest_for_video(video_id)
        if known is not None and not known.youtube_channel_id:
            known = None  # can't be checked against trusted channels
        partial = False
        meta: VideoMetadata | None
        if known is not None:
            meta = known.metadata()
        elif (shown := GATE.shown(raw_id)) is not None:
            meta, partial = shown, True
        else:
            found = self._fetch(profile_id, ("video", raw_id), 1, lambda n: [
                m for m in [self.backend.fetch_video_info(url)] if m])
            meta = found[0] if found else None
        if meta is None or not self.passes(meta, self.kid_rules(profile_id)):
            raise WishlistError("not_allowed")
        request_id = self.repo.add(profile_id, video_id, meta, meta.original_url or url)
        self.thumbnails.download(video_id, meta.thumbnail_url)
        return request_id, partial

    def fill_details(self, request_id: int) -> None:
        """Fetch YouTube's full details for a request recorded from search
        results, and turn it down if they break the rules (a blocked word
        in the description, say)."""
        item = self.repo.get(request_id)
        if item is None or item.status != "pending":
            return
        meta = GATE.video(item.raw_id, lambda: self.backend.fetch_video_info(item.url))
        if meta is None:
            # Private, removed or region-locked: nothing the parent could
            # add, so say so instead of leaving it waiting.
            error = getattr(self.backend, "last_error", None)
            if error is not None and error.error_type in _CANNOT_ADD:
                self.repo.decide(request_id, "unavailable")
            return
        self.repo.fill_details(request_id, meta)
        if not self.passes(meta, self.kid_rules(item.profile_id)):
            self.repo.decide(request_id, "rejected")


def fill_details_later(connect: Callable, backend: VideoBackend, thumbnails: ThumbnailService,
                       config: Config, request_id: int) -> None:
    """WishlistService.fill_details as a background task: it runs after the
    response, when the request's own database connection is closed."""
    conn = connect()
    try:
        WishlistService(backend, WishlistRepository(conn), VideoRepository(conn),
                        thumbnails, config).fill_details(request_id)
    except Exception:
        logger.exception("wishlist: fetching details for request %s failed", request_id)
    finally:
        conn.close()
