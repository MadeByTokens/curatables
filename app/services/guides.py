"""The animated guides: steps (a screenshot, what to tap, what it does)
made by tools/make_guides.py into app/static/guides/<guide>.json, played
on the Help pages."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

GUIDES_DIR = Path(__file__).resolve().parents[1] / "static" / "guides"
GUIDES = ("kids", "parents")


@lru_cache(maxsize=4)
def _load(guide: str) -> dict:
    try:
        data = json.loads((GUIDES_DIR / f"{guide}.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    return data if isinstance(data, dict) else {"tours": [], "langs": {}}


def _shows(needs: str, viewer) -> bool:
    """Whether a kid has what a tour shows: search on, or a switch."""
    if not needs or viewer is None or not viewer.is_child:
        return True
    if needs == "search":
        return bool(viewer.search_mode != "disabled")
    return bool(getattr(viewer, needs, True))


def tours(guide: str, lang: str, viewer=None) -> tuple[str, list[dict]]:
    """(the language the pictures are in, the tours with their icons),
    leaving out tours of things this kid can't do."""
    data = _load(guide)
    langs = data.get("langs", {})
    use = lang if lang in langs else "en"
    meta = {t["id"]: t for t in data.get("tours", [])}
    return use, [dict(t, icon=meta.get(t["id"], {}).get("icon", "")) for t in langs.get(use, [])
                 if _shows(meta.get(t["id"], {}).get("needs", ""), viewer)]


def tour(guide: str, lang: str, tour_id: str, viewer=None) -> tuple[str, dict | None]:
    use, found = tours(guide, lang, viewer)
    return use, next((t for t in found if t["id"] == tour_id), None)


def finger(box: list[float] | None) -> dict | None:
    """Where the finger goes on a step's picture, in percent of it."""
    if not box:
        return None
    x, y, w, h = box
    return {"left": round((x + w / 2) * 100, 2), "top": round((y + h / 2) * 100, 2)}
