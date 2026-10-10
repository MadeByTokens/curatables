"""Source-scanning guards for the kid UI and playback baseline.

- No ES6+ syntax in any kid-side ``<script>`` block or kid static JS. The
  kid UI must run on Safari iOS 9 / old Android WebView, so no ``const`` /
  ``let`` / ``=>`` / ``fetch(``.
- No static inline styles in templates.
- Only the watch and upload kid pages ship authored JS.
- The yt-dlp download format string prefers H.264 (``avc1``) + AAC
  (``mp4a``), so the common ingest needs no transcode.
"""

import re
from pathlib import Path


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


def _inline_scripts(text: str):
    """Yield (index, code) for each inline <script> block in ``text``.

    External ``<script src=...>`` tags are vendored libraries (e.g. tus),
    not authored kid JS, so they are skipped.
    """
    for i, m in enumerate(_SCRIPT_BLOCK.finditer(text)):
        opening = text[m.start():m.start() + m.group(0).index(">") + 1]
        if "src=" not in opening:
            yield i, m.group(1)


def _kid_script_sources():
    """Yield (label, code) for every kid-side script: inline blocks in kid
    templates plus any kid static .js file."""
    for html in sorted(KID_TEMPLATES.glob("*.html")):
        text = html.read_text(encoding="utf-8")
        for i, code in _inline_scripts(text):
            yield (f"{html.name} <script#{i}>", code)
    if KID_STATIC.exists():
        for js in sorted(KID_STATIC.glob("*.js")):
            yield (js.name, js.read_text(encoding="utf-8"))


def test_kid_js_is_es3():
    """No const/let/=>/fetch( in any kid-side script."""
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
    """No static inline style="" in base templates.

    An inline style="" is permitted only when its value contains a Jinja
    expression ({{ ... }}), i.e. the value is computed per-request and
    cannot live in a static class (e.g. a tag's font-size scaled by
    frequency, a per-channel banner colour). Every other inline style
    must move to a CSS class.
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
    """Only kid/watch and kid/upload may carry authored <script>.

    watch needs tiny ES3 XHR (reactions/logging must not reload the playing
    video); upload needs resumable multipart JS. Every other kid page must
    be zero-JS server-rendered.
    """
    offenders = []
    for html in sorted(KID_TEMPLATES.glob("*.html")):
        text = html.read_text(encoding="utf-8")
        for _, code in _inline_scripts(text):
            if code.strip() and html.name not in _KID_JS_ALLOWED:
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
