"""Iframe embed URLs for the parent Review & Add preview.

`embed_url_for()` returns an embeddable player URL for the allow-listed
platforms below, or None, which tells the template to fall back to an
open-in-new-tab link.

  - youtube      -> youtube-nocookie.com/embed/{id}
  - vimeo        -> player.vimeo.com/video/{id}
  - dailymotion  -> dailymotion.com/embed/video/{id}
  - peertube     -> {instance}/videos/embed/{uuid}
  - ted          -> embed.ted.com/talks/{slug}

Deliberately not embedded (link-out is the better UX):
  - TikTok    - full-branded card, not a pure player
  - Twitch    - clips need a `parent` param matching the serving host;
                VODs/livestreams have no simple iframe path
  - X/Twitter - JS widget only, tracks the reader
  - Instagram - embeds need Facebook Graph API auth for Reels
  - Facebook / Reddit - no clean iframe
"""

from __future__ import annotations

from urllib.parse import urlparse


def embed_url_for(extractor: str, raw_id: str,
                  original_url: str = "") -> str | None:
    """Return an iframe-safe embed URL for the given extractor, or None."""
    ex = (extractor or "").lower()
    if ex == "youtube":
        return f"https://www.youtube-nocookie.com/embed/{raw_id}"
    if ex == "vimeo":
        return f"https://player.vimeo.com/video/{raw_id}"
    if ex == "dailymotion":
        return f"https://www.dailymotion.com/embed/video/{raw_id}"
    if ex == "peertube":
        # PeerTube is federated - the embed URL is scoped to the
        # instance that hosts the video. Parse the host out of
        # original_url (populated from info["webpage_url"]).
        return _peertube_embed(raw_id, original_url)
    if ex == "ted":
        return f"https://embed.ted.com/talks/{raw_id}"
    return None


def _peertube_embed(uuid: str, original_url: str) -> str | None:
    if not original_url:
        return None
    parsed = urlparse(original_url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}/videos/embed/{uuid}"
