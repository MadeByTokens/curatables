"""The animated guides on the Help pages (made by tools/make_guides.py)."""

import json
import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile
from app.repositories import ProfileRepository
from app.services import guides

GUIDES = Path(__file__).resolve().parents[1] / "app" / "static" / "guides"


def _kid(app, **fields):
    conn = next(app.dependency_overrides[get_db]())
    pid = ProfileRepository(conn).create(Profile(name="mia", display_name="Mia", **fields))
    client = TestClient(app, follow_redirects=False)
    client.post("/profiles/select", data={"profile_id": pid})
    return client


def test_every_step_has_its_picture_and_every_picture_a_step():
    for guide in guides.GUIDES:
        data = json.loads((GUIDES / f"{guide}.json").read_text())
        assert data["langs"]["en"], guide
        ids = [t["id"] for t in data["tours"]]
        for lang, tours in data["langs"].items():
            assert [t["id"] for t in tours] == ids, (guide, lang)
            used = {s["img"] for t in tours for s in t["steps"]}
            on_disk = {p.name for p in (GUIDES / guide / lang).glob("*.webp")}
            assert used == on_disk, (guide, lang, used ^ on_disk)
            for t in tours:
                for s in t["steps"]:
                    assert s["say"].strip()
                    assert s["box"] is None or all(0 <= v <= 1.0001 for v in s["box"])


def test_a_kid_watches_how_in_their_language(app):
    kid = _kid(app, language="es", search_mode="curated", allow_wishlist=True)
    assert 'href="/help/guide"' in kid.get("/help").text
    page = kid.get("/help/guide").text
    assert "Buscar" in page and 'href="/help/guide/search"' in page
    tour = kid.get("/help/guide/home").text
    data = json.loads((GUIDES / "kids.json").read_text())
    steps = next(t for t in data["langs"]["es"] if t["id"] == "home")["steps"]
    # Every step is on the page, so it reads with no script too.
    assert tour.count('class="guide-step"') == len(steps)
    assert "/static/guides/kids/es/home-01.webp" in tour
    assert tour.count('class="guide-finger"') == sum(1 for s in steps if s["box"])
    assert '/static/kid/guide.js' in tour


def test_a_kid_sees_only_guides_for_what_they_can_do(app):
    kid = _kid(app, search_mode="disabled", allow_wishlist=False, allow_hide=False, allow_customize=False)
    page = kid.get("/help/guide").text
    for missing in ("search", "find-new", "hide", "channel-look"):
        assert f'href="/help/guide/{missing}"' not in page, missing
        assert kid.get(f"/help/guide/{missing}").status_code == 302
    assert 'href="/help/guide/home"' in page and 'href="/help/guide/channels"' in page
    assert kid.get("/help/guide/nonsense").headers["location"] == "/help/guide"


def test_parents_have_their_own_guides(app, authed_client, client):
    page = authed_client.get("/parent/help").text
    assert 'id="guides"' in page and 'href="/parent/help/guide/requests"' in page
    tour = authed_client.get("/parent/help/guide/requests").text
    assert "/static/guides/parents/en/requests-01.webp" in tour
    assert client.get("/parent/help/guide/requests").status_code in (302, 303)


def test_the_player_is_calm():
    """Reduce Motion stops the finger, and the one animation is a single
    slow press, never a flash."""
    css = (GUIDES.parent / "kid" / "guide.css").read_text()
    assert "@media (prefers-reduced-motion: reduce)" in css
    for m in re.finditer(r"animation:\s*[\w-]+\s+([\d.]+)s[^;]*?(\d+|infinite);", css):
        assert float(m.group(1)) >= 1 and m.group(2) == "1"
    js = (GUIDES.parent / "kid" / "guide.js").read_text()
    assert "prefers-reduced-motion" in js


def test_the_guides_show_the_screens_as_they_are():
    """Each guide records the screens it shows. A screen changed since its
    pictures were made means the guide is out of date: make it again with
    `python tools/make_guides.py --only GUIDE --tour ID` (see tools/README.md),
    and check its captions still say what the screen says."""
    stale = []
    for guide in guides.GUIDES:
        data = json.loads((GUIDES / f"{guide}.json").read_text())
        for tour in data["tours"]:
            assert tour.get("screens"), (guide, tour["id"], "made without its screens")
            for template, fingerprint in tour["screens"].items():
                if guides.screen_fingerprint(template) != fingerprint:
                    stale.append(f"{guide}/{tour['id']} ({template})")
    assert not stale, "guides out of date, make them again: " + ", ".join(stale)
