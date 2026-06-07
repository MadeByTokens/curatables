"""Per-extractor embed URL builder for the parent Review & Add page.

`embed_url_for()` returns an iframe-safe URL for the five platforms
in the Tier 1 allow-list, or `None` for everything else. Callers
(currently `parent/content_preview.html` via a Jinja global) use
`None` as the "show the open-in-new-tab fallback" signal.

Tier 1 iframe allow-list (ships in v1):
  - youtube      -> youtube-nocookie.com/embed/{id}
  - vimeo        -> player.vimeo.com/video/{id}
  - dailymotion  -> dailymotion.com/embed/video/{id}
  - peertube     -> {instance}/videos/embed/{uuid}
  - ted          -> embed.ted.com/talks/{slug}

Deliberately NOT in Tier 1 (link-out is the better UX for these):
  - TikTok    - full-branded card, not a pure player, rots fast
  - Twitch    - clips need a `parent` query param matching the
                serving host; VODs/livestreams have no simple
                iframe path at all
  - X/Twitter - no iframe, only a JS widget that injects a
                branded tweet card and tracks the reader
  - Instagram - /p/{id}/embed needs Facebook Graph API auth for
                Reels and rate-limits aggressively
  - Facebook / Reddit - same family, no clean iframe

Every platform in the allow-list is a thing we have to keep
working; embed URLs drift over time. Promoting a sixth later is
a 5-line edit in this file plus one test in tests/test_embeds.py.
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
