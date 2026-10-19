"""Trusted YouTube channels, by channel ID.

The family list applies to every kid; per kid, the parent adds channels
for that kid only or switches family ones off. Requests from a kid's
trusted channels may skip review, and a kid set to "only trusted
channels" finds nothing else (see WishlistService)."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit

from app.backends.base import ChannelHit, VideoBackend
from app.repositories.trusted_channel_repo import TrustedChannel, TrustedChannelRepository

CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
_HANDLE = re.compile(r"^@[\w.-]{1,100}$")
_NAME = re.compile(r"^[\w.-]{1,100}$")
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"}
NAME_MAX = 100


class TrustedChannelError(Exception):
    """A parent-facing reason a channel could not be added."""


def channel_link(text: str) -> str | None:
    """The YouTube page to look a channel up from: a channel ID, an
    @handle, or a channel or video link. None if it is none of these."""
    text = text.strip()
    if CHANNEL_ID.match(text):
        return f"https://www.youtube.com/channel/{text}"
    if _HANDLE.match(text):
        return f"https://www.youtube.com/{text}"
    if "://" not in text:
        text = "https://" + text
    try:
        parts = urlsplit(text)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https"):
        return None
    segs = [s for s in parts.path.split("/") if s]
    if host == "youtu.be":
        return f"https://www.youtube.com/watch?v={segs[0]}" if segs and _VIDEO_ID.match(segs[0]) else None
    if host not in _HOSTS or not segs:
        return None
    if segs[0] == "channel" and len(segs) > 1 and CHANNEL_ID.match(segs[1]):
        return f"https://www.youtube.com/channel/{segs[1]}"
    if _HANDLE.match(segs[0]):
        return f"https://www.youtube.com/{segs[0]}"
    if segs[0] in ("c", "user") and len(segs) > 1 and _NAME.match(segs[1]):
        return f"https://www.youtube.com/{segs[0]}/{segs[1]}"
    if segs[0] == "watch":
        vid = (parse_qs(parts.query).get("v") or [""])[0]
        return f"https://www.youtube.com/watch?v={vid}" if _VIDEO_ID.match(vid) else None
    if segs[0] in ("shorts", "live") and len(segs) > 1 and _VIDEO_ID.match(segs[1]):
        return f"https://www.youtube.com/watch?v={segs[1]}"
    return None


class TrustedChannelService:
    def __init__(self, repo: TrustedChannelRepository, backend: VideoBackend | None = None):
        self.repo = repo
        self.backend = backend

    def family(self) -> list[TrustedChannel]:
        return self.repo.family()

    def extra_for(self, profile_id: int) -> list[TrustedChannel]:
        return self.repo.extra_for(profile_id)

    def switched_off_for(self, profile_id: int) -> set[str]:
        return self.repo.switched_off_for(profile_id)

    def ids_for(self, profile_id: int) -> set[str]:
        return self.repo.ids_for(profile_id)

    def lookup(self, text: str) -> TrustedChannel:
        """The channel a pasted ID, @handle or link points to. YouTube is
        asked for its name (and, for anything but an ID, its ID)."""
        url = channel_link(text)
        if url is None:
            raise TrustedChannelError(
                "That isn't a YouTube channel or video link, an @handle or a channel ID.")
        hit = None
        if self.backend is not None:
            try:
                hit = self.backend.find_channel(url)
            except NotImplementedError:
                hit = None
        if hit is None:
            given = url.rsplit("/", 1)[-1]
            if CHANNEL_ID.match(given):
                # YouTube didn't answer, but the ID is all the rules need.
                return TrustedChannel(given, given)
            raise TrustedChannelError(
                "YouTube didn't say which channel that is. Check the link, or try again later.")
        return TrustedChannel(hit.channel_id, " ".join(hit.name.split())[:NAME_MAX] or hit.channel_id)

    def search(self, query: str) -> list[ChannelHit]:
        query = " ".join(query.split())[:100]
        if not query or self.backend is None:
            return []
        try:
            hits = self.backend.search_channels(query, max_results=12)
        except NotImplementedError:
            return []
        return [h for h in hits if CHANNEL_ID.match(h.channel_id)]

    def add(self, channel_id: str, name: str, profile_id: int | None = None) -> None:
        """Trust a channel for every kid (profile_id None; what any one kid
        had for it goes) or for one."""
        if not CHANNEL_ID.match(channel_id):
            raise TrustedChannelError("That isn't a YouTube channel ID.")
        name = " ".join(name.split())[:NAME_MAX] or channel_id
        if profile_id is None:
            self.repo.add_family(channel_id, name)
        else:
            self.repo.add_for(profile_id, channel_id, name)

    def remove(self, channel_id: str, profile_id: int | None = None) -> None:
        """Take a channel off for every kid, or off one kid's own list."""
        if profile_id is None:
            self.repo.remove_family(channel_id)
        else:
            self.repo.remove_for(profile_id, channel_id)

    def set_switched_off(self, profile_id: int, off: set[str]) -> None:
        self.repo.set_switched_off(profile_id, off)
