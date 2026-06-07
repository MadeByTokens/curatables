"""Guard tests for the playback & GUI delivery plan (docs/ui-and-playback-plan.md).

Two cross-cutting invariants, matched to the existing guard-test culture
(test_template_response_signature.py, the youtube-drift grep rule):

- **AC-NoES6**: no ES6+ syntax in any kid-side ``<script>`` block or kid
  static JS. The kid UI must run on Safari iOS 9 / old Android WebView —
  no ``const`` / ``let`` / ``=>`` / ``fetch(``.
- **Codec format guard**: the yt-dlp download format string must prefer
  H.264 (``avc1``) + AAC (``mp4a``), so the common ingest needs no
  transcode and the playback baseline holds at the source.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KID_TEMPLATES = REPO_ROOT / "app" / "templates" / "base" / "kid"
KID_STATIC = REPO_ROOT / "app" / "static" / "kid"

# Patterns that are ES6+ (or otherwise outside the iOS 9 baseline).
# Word-boundary anchored so they don't fire inside identifiers/comments-ish.
_ES6_PATTERNS = {
    "const": re.compile(r"\bconst\b"),
    "let": re.compile(r"\blet\b"),
    "arrow =>": re.compile(r"=>"),
    "fetch(": re.compile(r"\bfetch\s*\("),
}

_SCRIPT_BLOCK = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL | re.IGNORECASE)

_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
# Line comments, but not the "//" inside a "://" URL scheme.
_LINE_COMMENT = re.compile(r"(?<!:)//.*$", re.MULTILINE)


def _strip_comments(code: str) -> str:
    """Remove JS comments so prose ("no const/let") doesn't trip the scan."""
    return _LINE_COMMENT.sub("", _BLOCK_COMMENT.sub("", code))


def _kid_script_sources():
    """Yield (label, code) for every kid-side script: inline blocks in kid
    templates plus any kid static .js file."""
    for html in sorted(KID_TEMPLATES.glob("*.html")):
        text = html.read_text(encoding="utf-8")
        for i, m in enumerate(_SCRIPT_BLOCK.finditer(text)):
            # Skip external scripts (src=...) — those are vendored libs
            # (e.g. tus) checked separately; here we mean authored kid JS.
            opening = text[m.start():m.start() + m.group(0).index(">") + 1]
            if "src=" in opening:
                continue
            yield (f"{html.name} <script#{i}>", m.group(1))
    if KID_STATIC.exists():
        for js in sorted(KID_STATIC.glob("*.js")):
            yield (js.name, js.read_text(encoding="utf-8"))


def test_kid_js_is_es3():
    """AC-NoES6: no const/let/=>/fetch( in any kid-side script."""
    offenders = []
    for label, raw in _kid_script_sources():
        code = _strip_comments(raw)
        for name, pat in _ES6_PATTERNS.items():
            for lineno, line in enumerate(code.splitlines(), start=1):
                if pat.search(line):
                    offenders.append(f"{label} line {lineno}: {name!r} → {line.strip()[:80]}")
    assert not offenders, "ES6+ syntax in kid JS:\n" + "\n".join(offenders)


_BASE_TEMPLATES = REPO_ROOT / "app" / "templates" / "base"
_STYLE_ATTR = re.compile(r'style="([^"]*)"')


def test_no_static_inline_styles_in_templates():
    """M1 / Phase 2: no static inline style="" in base templates.

    Agreed allowlist: an inline style="" is permitted ONLY when its value
    contains a Jinja expression ({{ ... }}) — i.e. the value is computed
    per-request and genuinely cannot live in a static class (e.g. a tag's
    font-size scaled by frequency, a per-channel banner colour). Every
    other inline style must move to a CSS class.
    """
    offenders = []
    for html in sorted(_BASE_TEMPLATES.rglob("*.html")):
        text = html.read_text(encoding="utf-8")
        for m in _STYLE_ATTR.finditer(text):
            if "{{" not in m.group(1):
                lineno = text[:m.start()].count("\n") + 1
                rel = html.relative_to(REPO_ROOT)
                offenders.append(f"{rel}:{lineno}: style=\"{m.group(1)[:60]}\"")
    assert not offenders, (
        "static inline style= attrs (move to a CSS class):\n" + "\n".join(offenders)
    )


_KID_JS_ALLOWED = {"watch.html", "upload.html"}


def test_only_watch_and_upload_ship_kid_js():
    """M4 / M7: only kid/watch and kid/upload may carry authored <script>.

    watch needs tiny ES3 XHR (reactions/logging must not reload the playing
    video); upload needs resumable multipart JS. Every other kid page must
    be zero-JS server-rendered (e.g. video_edit's thumbnail preview is now
    a server round-trip).
    """
    offenders = []
    for html in sorted(KID_TEMPLATES.glob("*.html")):
        text = html.read_text(encoding="utf-8")
        # authored inline script (ignore <script src=...> vendored libs)
        for m in _SCRIPT_BLOCK.finditer(text):
            opening = text[m.start():m.start() + m.group(0).index(">") + 1]
            if "src=" in opening:
                continue
            if m.group(1).strip() and html.name not in _KID_JS_ALLOWED:
                offenders.append(html.name)
                break
    assert not offenders, (
        "kid pages shipping JS outside the watch/upload budget: " + ", ".join(offenders)
    )


def test_ytdlp_format_prefers_h264_aac():
    """The download format string must prefer avc1 (H.264) + mp4a (AAC)."""
    src = (REPO_ROOT / "app" / "backends" / "ytdlp.py").read_text(encoding="utf-8")
    assert "avc1" in src, "yt-dlp format string must prefer avc1 (H.264)"
    assert "mp4a" in src, "yt-dlp format string must prefer mp4a (AAC)"
