"""How children with special needs, autistic children among them, use
the kid screens: the same tap over and over, a Home that must stay as
they left it, a quick finger on the PIN pictures, sensitivity to motion
and sound, and a calm way back from anything unexpected.

The browser side of the same ideas (holding a page for a long time,
mashing Back, replaying a video) is in tests/box/personas.py, run
against a whole box; see docs/testing/README.md.
"""

import re
from pathlib import Path

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.repositories.hidden_video_repo import HiddenVideoRepository
from app.services.profiles import ProfileService

KID_STATIC = Path(__file__).resolve().parent.parent / "app" / "static" / "kid"
TEMPLATES = Path(__file__).resolve().parent.parent / "app" / "templates" / "base" / "kid"


def _setup(app, n=4, **flags):
    conn = next(app.dependency_overrides[get_db]())
    ch = ChannelRepository(conn).create("Trains")
    videos = VideoRepository(conn)
    for i in range(n):
        _add_video(conn, videos, i, ch, minutes_ago=n - i)
    pid = ProfileRepository(conn).create(Profile(name="sam", display_name="Sam", **flags))
    kid = TestClient(app, follow_redirects=False)
    kid.post("/profiles/select", data={"profile_id": pid})
    return conn, kid, pid, ch


def _add_video(conn, videos, i, ch, minutes_ago=0):
    videos.insert(Video(video_id=f"yt_{i}", title=f"Train {i}", original_title=f"Train {i}",
                        channel_id=ch, status="active", download_status="ready"))
    conn.execute("UPDATE videos SET added_at = datetime('now', ?) WHERE video_id = ?",
                 (f"-{minutes_ago} minutes", f"yt_{i}"))
    conn.commit()


def _home(kid):
    page = kid.get("/").text
    grid = page[page.index('class="video-grid home-grid'):]
    return re.findall(r"/media/thumb/(yt_\d+)", grid)


def test_an_arranged_home_stays_put_when_new_videos_arrive(app):
    """Routine matters: a Home the kid arranged never shifts by itself."""
    conn, kid, _, ch = _setup(app)
    kid.post("/home/move", data={"source": "0", "target": "3"})
    arranged = _home(kid)
    videos = VideoRepository(conn)
    for i in range(10, 14):
        _add_video(conn, videos, i, ch)
    assert _home(kid) == arranged
    assert "/watch/yt_13" in kid.get("/all").text


def test_the_same_tap_over_and_over_settles(app, authed_client):
    """Tapping one button dozens of times ends in a sensible state,
    never in duplicates or an error."""
    conn, kid, pid, _ = _setup(app)
    for _ in range(51):
        resp = kid.post("/api/react", data={"video_id": "yt_1", "emoji": "love"})
        assert resp.status_code == 200
    assert resp.json() == {"mine": "love", "counts": {"love": 1}}

    for _ in range(15):
        assert kid.post("/home/remove", data={"video_id": "yt_2", "back": "watch"}).status_code == 303
        assert kid.post("/home/add", data={"video_id": "yt_2", "back": "watch"}).status_code == 303
    assert _home(kid).count("yt_2") == 1

    for _ in range(10):
        kid.post("/video/yt_3/hide")
        kid.post("/video/yt_3/unhide")
    kid.post("/video/yt_3/hide")
    assert len(HiddenVideoRepository(conn).for_profile(pid)) == 1
    dashboard = re.sub(r"<[^>]+>|\s+", " ", authed_client.get("/parent/").text)
    assert re.search(r"\b1 video a kid hid", dashboard)


def test_home_is_the_first_button_on_every_kid_page(app):
    """The way back is always in the same place."""
    conn, kid, _, ch = _setup(app, allow_customize=True, allow_tags=True, allow_wishlist=True,
                              search_mode="library")
    pages = ["/", "/?edit=1", "/all", f"/channel/{ch}", "/watch/yt_1", "/video/yt_1/edit",
             "/search?q=train", "/tags", "/wishlist", "/help", "/home/pick?slot=0"]
    for path in pages:
        resp = kid.get(path)
        assert resp.status_code == 200, path
        nav = re.search(r'<nav class="header-nav"[^>]*>(.*?)</nav>', resp.text, re.S)
        assert nav, path
        first = re.search(r'href="([^"]*)"', nav.group(1)).group(1)
        assert first == "/", (path, first)


def test_something_unexpected_is_calm_and_has_a_way_home(app):
    """A missing page, an old address, a video that is gone: no codes or
    technical words, and a big Home button."""
    _, kid, _, _ = _setup(app)
    for path in ("/no/such/page", "/watch/yt_gone", "/channel/9999", "/video/yt_gone/edit"):
        resp = kid.get(path)
        if resp.status_code in (302, 303):
            assert resp.headers["location"] in ("/", "/profiles"), path
            continue
        page = resp.text
        assert 'href="/"' in page, path
        for word in ("404", "405", "500", "Method Not Allowed", "detail", "Traceback", "Error"):
            assert word not in re.sub(r"<[^>]+>", " ", page), (path, word)


def test_wrong_pictures_lock_kindly_and_only_that_profile(app):
    """A quick finger taps the wrong pictures: after a few tries the PIN
    page says to wait, without blame, and offers Back; a sibling on the
    same tablet is not locked out."""
    conn = next(app.dependency_overrides[get_db]())
    svc = ProfileService(ProfileRepository(conn))
    pid = svc.create(name="mia", display_name="Mia", pin="512", pin_style="pictures")
    sib = svc.create(name="leo", display_name="Leo")
    tablet = TestClient(app, follow_redirects=False)
    for _ in range(5):
        tablet.post("/profiles/pin", data={"profile_id": pid, "pin": "999"})
    locked = tablet.post("/profiles/pin", data={"profile_id": pid, "pin": "512"}).text
    assert "Wait a little" in locked
    assert 'class="pin-pad' not in locked
    assert 'href="/profiles"' in locked
    words = re.sub(r"<[^>]+>", " ", locked).lower()
    for harsh in ("wrong", "invalid", "denied", "error", "failed", "blocked"):
        assert harsh not in words, harsh
    assert tablet.post("/profiles/select", data={"profile_id": sib}).status_code == 302


def test_videos_wait_for_a_tap_to_start(app):
    """No sound out of nowhere: the player never starts by itself."""
    _, kid, _, _ = _setup(app)
    page = kid.get("/watch/yt_1").text
    assert "autoplay" not in page
    js = (KID_STATIC / "watch.js").read_text()
    # Every play() is inside a tap handler (onclick), never at load.
    for m in re.finditer(r"video\.play\(\)", js):
        before = js[:m.start()]
        assert before.rfind("onclick") > before.rfind("\n  }"), js[m.start() - 200:m.start()]


def test_reduced_motion_stops_every_animation():
    """A tablet set to Reduce Motion gets no movement at all: one rule
    switches everything off, and no stylesheet overrides it."""
    style = (KID_STATIC / "style.css").read_text()
    block = re.search(r"@media \(prefers-reduced-motion: reduce\)\s*\{(.*?)\n\}", style, re.S)
    assert block and "animation: none !important" in block.group(1)
    assert "transition: none !important" in block.group(1)
    for css in KID_STATIC.glob("*.css"):
        text = css.read_text()
        outside = text.replace(block.group(0), "") if css.name == "style.css" else text
        assert not re.search(r"(animation|transition)[^;{}]*!important", outside), css.name


def test_nothing_on_kid_screens_flashes_fast():
    """Blinking or fast flashing can upset or harm: no animation in the
    kid stylesheets repeats faster than about three times a second."""
    for css in KID_STATIC.glob("*.css"):
        for m in re.finditer(r"animation:\s*[\w-]+\s+([\d.]+)(m?s)", css.read_text()):
            seconds = float(m.group(1)) / (1000 if m.group(2) == "ms" else 1)
            assert seconds >= 0.34, (css.name, m.group(0))
    for tpl in TEMPLATES.glob("*.html"):
        assert "<blink" not in tpl.read_text() and "<marquee" not in tpl.read_text(), tpl.name
