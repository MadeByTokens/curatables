"""Regression guard: no `TemplateResponse(` call in app/ may omit the
explicit `request` as first positional argument.

Background: Starlette 1.0 removed the deprecated
`TemplateResponse(name, context)` signature. Every call site must use
`TemplateResponse(request, name, context)`. The old form silently 500s
on Starlette >= 1.0 (Jinja2 cache trips on `TypeError: unhashable type:
'dict'` because the context dict ends up in the cache-key position).

This test scans all Python sources under `app/` and fails if any
`TemplateResponse(` call is not immediately followed by a token that
could be `request` (either the literal identifier `request` or a
line-continuation to the next line whose first non-whitespace token is
`request`).
"""

from __future__ import annotations

import io
import re
import tokenize
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent / "app"


def _iter_call_sites(source: str, path: Path):
    """Yield (lineno, first_arg_text) for every `TemplateResponse(...)`
    call in ``source``. ``first_arg_text`` is the literal text of the
    first positional argument as it appears in the source, stripped of
    whitespace.

    Uses ``tokenize`` so comments and docstrings don't register as
    calls, and so multi-line calls are handled correctly.
    """
    tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    for idx, tok in enumerate(tokens):
        if tok.type != tokenize.NAME or tok.string != "TemplateResponse":
            continue
        # Must be immediately followed by `(`.
        if idx + 1 >= len(tokens):
            continue
        nxt = tokens[idx + 1]
        if nxt.type != tokenize.OP or nxt.string != "(":
            continue
        # The token after `(` should be the first positional arg.
        if idx + 2 >= len(tokens):
            continue
        first = tokens[idx + 2]
        # Skip over any NL / NEWLINE / INDENT tokens between `(` and
        # the first real arg token (handles multi-line calls).
        j = idx + 2
        while first.type in (tokenize.NL, tokenize.NEWLINE,
                             tokenize.INDENT, tokenize.DEDENT):
            j += 1
            if j >= len(tokens):
                break
            first = tokens[j]
        yield tok.start[0], first.string


def test_no_old_templateresponse_signature():
    offenders: list[str] = []
    for py in APP_ROOT.rglob("*.py"):
        source = py.read_text()
        if "TemplateResponse(" not in source:
            continue
        for lineno, first_arg in _iter_call_sites(source, py):
            if first_arg != "request":
                rel = py.relative_to(APP_ROOT.parent)
                offenders.append(
                    f"{rel}:{lineno}: first arg is {first_arg!r}, "
                    "expected `request` (Starlette 1.0 signature)"
                )
    assert not offenders, (
        "Found TemplateResponse calls using the deprecated "
        "`TemplateResponse(name, context)` signature. Migrate them to "
        "`TemplateResponse(request, name, context)`:\n  "
        + "\n  ".join(offenders)
    )


def test_guard_detects_bad_signature(tmp_path):
    """Sanity: the guard test above must actually catch the old form.

    Otherwise it would silently pass even after a regression.
    """
    bad = tmp_path / "bad.py"
    bad.write_text(
        "from fastapi.templating import Jinja2Templates\n"
        "templates = Jinja2Templates('x')\n"
        "def view(request):\n"
        "    return templates.TemplateResponse('foo.html', {})\n"
    )
    source = bad.read_text()
    calls = list(_iter_call_sites(source, bad))
    assert calls, "detector should find the call"
    lineno, first_arg = calls[0]
    assert first_arg != "request"
    # And a well-formed call parses correctly too.
    good = tmp_path / "good.py"
    good.write_text(
        "from fastapi.templating import Jinja2Templates\n"
        "templates = Jinja2Templates('x')\n"
        "def view(request):\n"
        "    return templates.TemplateResponse(request, 'foo.html', {})\n"
    )
    good_source = good.read_text()
    good_calls = list(_iter_call_sites(good_source, good))
    assert good_calls and good_calls[0][1] == "request"


def test_starlette_version_supports_new_signature():
    """If starlette drops below 0.29, the new signature raises. Prefer
    failing in unit tests to debugging 500s in production.
    """
    import starlette
    # Parse "major.minor" only; dev builds may suffix with ".postN".
    m = re.match(r"^(\d+)\.(\d+)", starlette.__version__)
    assert m, f"unrecognised starlette version: {starlette.__version__}"
    major, minor = int(m.group(1)), int(m.group(2))
    # 0.29 introduced TemplateResponse(request, name, ...); 1.0 removed the old form.
    assert (major, minor) >= (0, 29), (
        f"starlette {starlette.__version__} predates the "
        "TemplateResponse(request, name, context) signature. "
        "Bump `starlette>=0.29` in requirements.txt."
    )
