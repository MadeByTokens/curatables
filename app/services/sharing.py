from __future__ import annotations
"""Shared-curation service — encode / decode / render channel exports.

Three formats:
- .ytc  — canonical JSON (schema: "curatables.ytc/1"), round-trippable.
- text  — one URL per line, `#` for comments, blank lines ignored.
- PDF   — printable list via reportlab. Warn-and-degrade: if reportlab
          isn't importable, render_pdf raises SharingUnavailable and
          callers surface a clean error. The .ytc and text paths keep
          working.

Security posture: the importer RE-FETCHES metadata from the source
(yt-dlp) — hints in the file are only used if the re-fetch fails.
This closes the attack surface where a malicious .ytc injects fake
titles/descriptions into the parent's preview page.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable

from app.models.channel import Channel
from app.models.video import Video


SCHEMA_ID = "curatables.ytc/1"


class SharingError(Exception):
    """Base error for all sharing encode/decode failures."""


class SharingUnavailable(SharingError):
    """Raised when an optional dependency (reportlab for PDF) isn't installed."""


@dataclass
class ImportEntry:
    """One row from a decoded .ytc / text file.

    `url` is the only field required by downstream importers; the
    hints are ignored unless the re-fetch fails (future work — today
    we always re-fetch).
    """
    url: str
    title_hint: str = ""
    description_hint: str = ""


@dataclass
class ImportPayload:
    """Result of decoding a shared-curation file."""
    channel_name: str = ""
    channel_description: str = ""
    channel_color: str = ""
    entries: list[ImportEntry] = field(default_factory=list)
    source_format: str = ""   # "ytc" | "text" — for diagnostics only.


# ---------------------------------------------------------------------------
# .ytc (JSON) format
# ---------------------------------------------------------------------------

def encode_ytc(channel: Channel, videos: Iterable[Video]) -> dict:
    """Build the JSON-serialisable dict for a .ytc v1 export.

    Deliberately emits only URLs + lightweight hints. No thumbnails,
    no file bytes, no cached metadata — keeps files tiny and forces
    the importer to re-fetch current source metadata.
    """
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "schema": SCHEMA_ID,
        "exported_at": now,
        "channel": {
            "name": channel.name or "",
            "description": channel.description or "",
            "color": channel.color or "",
        },
        "videos": [
            {
                "url": v.original_url,
                "title": v.title,
                "description": v.description[:500] if v.description else "",
                "source_title": v.channel_name or "",
                "extractor": v.extractor or "",
                "added_at": v.added_at or "",
            }
            for v in videos
            # Skip videos with no source URL — they wouldn't round-trip.
            if v.original_url
        ],
    }


def encode_ytc_bytes(channel: Channel, videos: Iterable[Video]) -> bytes:
    """UTF-8 JSON bytes ready to serve as a download."""
    payload = encode_ytc(channel, videos)
    return json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")


def decode_ytc(raw: bytes | str) -> ImportPayload:
    """Parse a .ytc v1 byte string or str into an ImportPayload.

    Strict on schema version (rejects anything but SCHEMA_ID) so a
    future v2 can't be silently mis-imported as v1. Lenient on
    unknown keys within v1 so we can add fields in a v1.x update
    without breaking older importers.
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SharingError(f"File is not valid UTF-8: {e}") from e
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise SharingError(f"File is not valid JSON: {e}") from e
    if not isinstance(data, dict):
        raise SharingError("Expected a JSON object at the top level.")

    schema = data.get("schema")
    if schema != SCHEMA_ID:
        if schema is None:
            raise SharingError(
                "Missing \"schema\" field — not a Curatables .ytc file."
            )
        raise SharingError(
            f"Unsupported schema {schema!r}. This file was made with a "
            f"different version of Curatables; upgrade before importing."
        )

    ch = data.get("channel") or {}
    if not isinstance(ch, dict):
        raise SharingError("\"channel\" must be an object.")
    videos = data.get("videos") or []
    if not isinstance(videos, list):
        raise SharingError("\"videos\" must be a list.")

    entries: list[ImportEntry] = []
    for row in videos:
        if not isinstance(row, dict):
            continue  # forward-compat: silently skip malformed rows
        url = (row.get("url") or "").strip()
        if not url:
            continue
        entries.append(ImportEntry(
            url=url,
            title_hint=str(row.get("title") or "")[:500],
            description_hint=str(row.get("description") or "")[:2000],
        ))

    return ImportPayload(
        channel_name=str(ch.get("name") or "").strip(),
        channel_description=str(ch.get("description") or "").strip(),
        channel_color=str(ch.get("color") or "").strip(),
        entries=entries,
        source_format="ytc",
    )


# ---------------------------------------------------------------------------
# Plain text format
# ---------------------------------------------------------------------------

def render_text(channel: Channel, videos: Iterable[Video]) -> str:
    """Render a channel as a plain-text URL list with header comments.

    The exported form round-trips through `parse_text` — leading
    `# comment` lines preserve the channel name and description for
    hand-editing; the URL list is one per line.
    """
    lines = [
        f"# Curatables channel export",
        f"# name: {channel.name}",
    ]
    if channel.description:
        for chunk in channel.description.splitlines():
            lines.append(f"# description: {chunk}")
    lines.append("")
    for v in videos:
        if not v.original_url:
            continue
        lines.append(v.original_url)
        if v.title:
            lines.append(f"  # {v.title}")
    return "\n".join(lines) + "\n"


def parse_text(raw: bytes | str) -> ImportPayload:
    """Parse a plain-text import file.

    Format rules (kept deliberately boring):
      - blank lines ignored
      - lines starting with `#` are comments
      - `# name: X` comment header pre-fills the channel name hint
      - everything else is treated as a URL; basic `http(s)://` sanity check
    """
    if isinstance(raw, bytes):
        try:
            raw = raw.decode("utf-8")
        except UnicodeDecodeError as e:
            raise SharingError(f"File is not valid UTF-8: {e}") from e

    channel_name = ""
    channel_description_parts: list[str] = []
    entries: list[ImportEntry] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            # Recognise the two header hints the exporter emits.
            body = stripped.lstrip("#").strip()
            if body.lower().startswith("name:"):
                channel_name = body.split(":", 1)[1].strip()
            elif body.lower().startswith("description:"):
                channel_description_parts.append(body.split(":", 1)[1].strip())
            continue
        # Not a comment — must look like a URL.
        if not (stripped.startswith("http://") or stripped.startswith("https://")):
            continue
        entries.append(ImportEntry(url=stripped))

    return ImportPayload(
        channel_name=channel_name,
        channel_description="\n".join(channel_description_parts),
        channel_color="",
        entries=entries,
        source_format="text",
    )


# ---------------------------------------------------------------------------
# PDF (warn-and-degrade)
# ---------------------------------------------------------------------------

def pdf_available() -> bool:
    """True iff reportlab is importable. Callers can branch on this
    instead of try/except-ing the render call."""
    try:
        import reportlab  # noqa: F401
        return True
    except ImportError:
        return False


def render_pdf(channel: Channel, videos: Iterable[Video]) -> bytes:
    """Render a channel as a printable PDF.

    Raises SharingUnavailable if reportlab isn't installed — callers
    should surface a clean error pointing at `pip install reportlab`.
    """
    try:
        from reportlab.lib.pagesizes import LETTER
        from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
        from reportlab.lib.units import inch
        from reportlab.platypus import (
            SimpleDocTemplate, Paragraph, Spacer, ListFlowable, ListItem,
        )
    except ImportError as e:
        raise SharingUnavailable(
            "PDF export requires reportlab. Install with: "
            "pip install 'reportlab>=4.0,<5.0'"
        ) from e

    import io
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=LETTER,
        leftMargin=0.75 * inch, rightMargin=0.75 * inch,
        topMargin=0.75 * inch, bottomMargin=0.75 * inch,
        title=f"Curatables — {channel.name}",
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "title", parent=styles["Title"], fontSize=20, spaceAfter=8)
    desc_style = ParagraphStyle(
        "desc", parent=styles["BodyText"], fontSize=11, textColor="#555555",
        spaceAfter=14)
    item_style = ParagraphStyle(
        "item", parent=styles["BodyText"], fontSize=11, leading=14)
    url_style = ParagraphStyle(
        "url", parent=styles["BodyText"], fontSize=9, textColor="#888888",
        leading=11)

    story = []
    story.append(Paragraph(_escape(channel.name or "(unnamed channel)"), title_style))
    if channel.description:
        story.append(Paragraph(_escape(channel.description), desc_style))
    story.append(Spacer(1, 4))

    items = []
    for v in videos:
        if not v.original_url:
            continue
        title = _escape(v.title or "(no title)")
        src = _escape(v.channel_name or v.extractor or "")
        url = _escape(v.original_url)
        body = f"<b>{title}</b>"
        if src:
            body += f"  <font color='#888888'>— {src}</font>"
        items.append(ListItem([
            Paragraph(body, item_style),
            Paragraph(url, url_style),
        ], leftIndent=14))

    if items:
        story.append(ListFlowable(items, bulletType="bullet", start="•",
                                  leftIndent=12, bulletFontSize=10))
    else:
        story.append(Paragraph("<i>No videos in this channel.</i>", desc_style))

    doc.build(story)
    return buf.getvalue()


def _escape(text: str) -> str:
    """Minimal reportlab markup-safe escape (& < > only)."""
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
