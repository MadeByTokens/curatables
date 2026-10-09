"""Subtitle text: decode, convert SubRip to WebVTT, name the language.

Browsers only play WebVTT in a <track>, so every subtitle is served as
VTT whatever it was stored as.
"""
from __future__ import annotations

import re

_SRT_TIME = re.compile(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})")
_SRT_TIMING = re.compile(
    r"^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})")
_ASS_OVERRIDE = re.compile(r"\{\\[^}]*\}")
_ALLOWED_TAG = re.compile(r"</?(?:i|b|u)>", re.IGNORECASE)
_ANY_TAG = re.compile(r"<[^>]*>")
_VTT_CUE_TIMING = re.compile(r"^(\d{2}:\d{2}:\d{2}\.\d{3})\s+-->\s+(\d{2}:\d{2}:\d{2}\.\d{3})")

# ISO 639-2 (what MKV tags carry) to the 639-1 codes a <track srclang> wants.
_THREE_TO_TWO = {
    "eng": "en", "por": "pt", "spa": "es", "fre": "fr", "fra": "fr", "ger": "de",
    "deu": "de", "ita": "it", "jpn": "ja", "chi": "zh", "zho": "zh", "kor": "ko",
    "rus": "ru", "dut": "nl", "nld": "nl", "swe": "sv", "nor": "no", "dan": "da",
    "fin": "fi", "pol": "pl", "tur": "tr", "gre": "el", "ell": "el", "heb": "he",
    "ara": "ar", "hin": "hi", "cze": "cs", "ces": "cs", "hun": "hu", "rum": "ro",
    "ron": "ro", "ukr": "uk", "cat": "ca", "glg": "gl", "baq": "eu", "eus": "eu",
}

LANGUAGE_NAMES = {
    "en": "English", "pt": "Português", "pt-br": "Português (Brasil)",
    "pt-pt": "Português (Portugal)", "es": "Español", "fr": "Français",
    "de": "Deutsch", "it": "Italiano", "ja": "日本語", "zh": "中文", "ko": "한국어",
    "ru": "Русский", "nl": "Nederlands", "sv": "Svenska", "no": "Norsk",
    "da": "Dansk", "fi": "Suomi", "pl": "Polski", "tr": "Türkçe", "el": "Ελληνικά",
    "he": "עברית", "ar": "العربية", "hi": "हिन्दी", "cs": "Čeština", "hu": "Magyar",
    "ro": "Română", "uk": "Українська", "ca": "Català", "gl": "Galego", "eu": "Euskara",
}


def language_code(raw: str) -> str:
    """A tidy BCP 47-ish code: 'eng' -> 'en', 'pt_BR' -> 'pt-BR', junk -> ''."""
    raw = (raw or "").strip().replace("_", "-")
    if not re.fullmatch(r"[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8})?", raw):
        return ""
    base, _, region = raw.partition("-")
    base = _THREE_TO_TWO.get(base.lower(), base.lower())
    if base in ("und", "unk", "mis", "zxx"):
        return ""
    return f"{base}-{region.upper()}" if region else base


def language_label(code: str) -> str:
    code = language_code(code)
    if not code:
        return "Subtitles"
    return LANGUAGE_NAMES.get(code.lower()) or LANGUAGE_NAMES.get(code.split("-")[0]) or code


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM) or UTF-16 when marked; otherwise
    Windows-1252, which is what most older .srt files are."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        text = data.decode("cp1252", errors="replace")
    return text.lstrip("﻿")


def _vtt_time(stamp: str) -> str:
    m = _SRT_TIME.match(stamp)
    if not m:
        return stamp
    h, mnt, sec, ms = m.groups()
    return f"{int(h):02d}:{mnt}:{sec}.{ms.ljust(3, '0')}"


def _clean_cue_text(line: str) -> str:
    line = _ASS_OVERRIDE.sub("", line)
    kept: list[str] = []

    def keep(match: re.Match) -> str:
        kept.append(match.group(0).lower())
        return f"\x00{len(kept) - 1}\x00"

    line = _ALLOWED_TAG.sub(keep, line)
    line = _ANY_TAG.sub("", line)
    line = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"\x00(\d+)\x00", lambda m: kept[int(m.group(1))], line)


def srt_to_vtt(text: str) -> str:
    """Convert SubRip text to WebVTT. Cue numbers are dropped, times get
    a dot, ASS-style override tags and anything but <i>/<b>/<u> are
    stripped, and blank-line structure is kept."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    out = ["WEBVTT", ""]
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = block.split("\n")
        timing_at = next((i for i, line in enumerate(lines) if _SRT_TIMING.match(line)), None)
        if timing_at is None:
            continue
        m = _SRT_TIMING.match(lines[timing_at])
        assert m is not None
        out.append(f"{_vtt_time(m.group(1))} --> {_vtt_time(m.group(2))}")
        body = [_clean_cue_text(line) for line in lines[timing_at + 1:]]
        out.extend(line for line in body if line.strip())
        out.append("")
    return "\n".join(out)


def normalize_vtt(raw: str) -> str:
    """Strip cue settings (position / align / line / size) from a VTT.

    YouTube auto-captions ship with `align:start position:0%` on every
    cue, which anchors the text at the left edge of the video and
    causes native HTML5 captions to render off-center. Removing the
    settings lets the browser fall back to the centered default.
    """
    out = []
    for line in raw.lstrip("﻿").splitlines():
        m = _VTT_CUE_TIMING.match(line)
        out.append(f"{m.group(1)} --> {m.group(2)}" if m else line)
    return "\n".join(out)


def to_vtt(data: bytes, suffix: str) -> str:
    """Any stored subtitle file as servable WebVTT."""
    text = decode_text(data)
    if suffix.lower() == ".srt" or not text.lstrip().startswith("WEBVTT"):
        return srt_to_vtt(text)
    return normalize_vtt(text)
