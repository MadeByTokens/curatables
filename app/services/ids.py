"""Composite video-id helpers.

The stored `video_id` is `{extractor}_{sanitised_raw_id}` so that:

1. Two different platforms that happen to issue the same raw ID
   can never collide (YouTube's 11-char alphanumeric space and
   Vimeo's integer space would otherwise overlap in principle).
2. The same string is safe to use as a filesystem directory name
   under `videos/` and as a URL path segment on `/watch/<id>`
   without any escaping.
3. The extractor is legible at a glance in logs, filesystem
   listings, and parent-side admin URLs.

Every site that persists or routes on a video ID goes through
`make_video_id()`; `sanitize_id_component()` is the raw sanitiser
reused for both the extractor part and the raw-id part.
"""

from __future__ import annotations

import re

_SAFE = re.compile(r"[^a-zA-Z0-9_-]")


def sanitize_id_component(raw: str) -> str:
    """Keep only `[a-zA-Z0-9_-]`, collapse runs of `-`, trim, cap length."""
    clean = _SAFE.sub("-", raw or "")
    while "--" in clean:
        clean = clean.replace("--", "-")
    clean = clean.strip("-")[:96]
    return clean or "x"


def make_video_id(extractor: str, raw_id: str) -> str:
    """Compose the stored video_id from an extractor name + the raw ID
    yt-dlp returned for the video.

    Examples:
        make_video_id("youtube", "dQw4w9WgXcQ") -> "youtube_dQw4w9WgXcQ"
        make_video_id("vimeo",   "123456789")   -> "vimeo_123456789"
        make_video_id("", "")                   -> "unknown_x"
    """
    ex = sanitize_id_component(extractor) if extractor else "unknown"
    if ex == "x":
        ex = "unknown"
    rid = sanitize_id_component(raw_id)
    return f"{ex}_{rid}"
