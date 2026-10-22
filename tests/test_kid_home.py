"""Each kid arranges their own Home, like a tablet's home screen."""

import re

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.services.kid_home import NEWEST_ON_A_NEW_HOME, SLOTS


def _setup(app, n=3):
    conn = next(app.dependency_overrides[get_db]())
    channels = ChannelRepository(conn)
    trains, cats = channels.create("Trains"), channels.create("Cats")
    videos = VideoRepository(conn)
    for i in range(n):
        videos.insert(Video(video_id=f"yt_{i}", title=f"Video {i}", original_title=f"Video {i}",
                            channel_id=trains if i % 2 else cats,
                            status="active", download_status="ready"))
        conn.execute("UPDATE videos SET added_at = datetime('now', ?) WHERE video_id = ?",
                     (f"-{n - i} minutes", f"yt_{i}"))
    conn.commit()
    profiles = ProfileRepository(conn)
    kid = TestClient(app, follow_redirects=False)
    kid.post("/profiles/select", data={"profile_id": profiles.create(Profile(name="sam"))})
    sibling = TestClient(app, follow_redirects=False)
    sibling.post("/profiles/select", data={"profile_id": profiles.create(Profile(name="ivy"))})
    return kid, sibling


def _home(client, query=""):
    """The video in each spot of Home, None for an empty one."""
    page = client.get("/" + query).text
    grid = page[page.index('class="video-grid home-grid'):]
    cards = re.findall(r'<(?:div|a|form) [^>]*class="video-card([^"]*)"(.*?)(?=<(?:div|a|form) [^>]*class="video-card|<div class="clear")',
                       grid, re.S)
    return [None if "home-gap" in cls or "home-empty" in cls
            else re.search(r"/media/thumb/(yt_\d+)", body).group(1) for cls, body in cards]


def test_a_new_home_shows_the_newest_videos_and_follows_new_ones(app):
    kid, _ = _setup(app, n=NEWEST_ON_A_NEW_HOME + 2)
    newest = [f"yt_{i}" for i in range(NEWEST_ON_A_NEW_HOME + 1, 1, -1)]
    assert _home(kid) == newest
    page = kid.get("/").text
    assert 'href="/channels"' in page and 'href="/channel/' not in page
    assert 'href="/?edit=1"' in page


def test_arranging_home(app):
    kid, sibling = _setup(app)
    assert _home(kid) == ["yt_2", "yt_1", "yt_0"]

    # Taking one off saves the layout; the spot stays empty.
    kid.post("/home/remove", data={"video_id": "yt_1"})
    assert _home(kid) == ["yt_2", None, "yt_0"]
    editing = _home(kid, "?edit=1")
    assert len(editing) == SLOTS and editing[:3] == ["yt_2", None, "yt_0"]
    assert 'href="/home/pick?slot=1"' in kid.get("/?edit=1").text

    # Move the first one far away; the gaps stay where the kid left them.
    assert 'name="target" value="5"' in kid.get("/?edit=1&move=0").text
    kid.post("/home/move", data={"source": "0", "target": "5"})
    assert _home(kid) == [None, None, "yt_0", None, None, "yt_2"]
    # Moving onto another video swaps the two.
    kid.post("/home/move", data={"source": "5", "target": "2"})
    assert _home(kid) == [None, None, "yt_2", None, None, "yt_0"]

    # Pick a video for an empty spot; one already on Home moves there.
    pick = kid.get("/home/pick?slot=0").text
    assert 'name="video_id" value="yt_1"' in pick and "On your Home" in pick
    kid.post("/home/add", data={"video_id": "yt_1", "slot": "0"})
    kid.post("/home/add", data={"video_id": "yt_0", "slot": "1"})
    assert _home(kid) == ["yt_1", "yt_0", "yt_2"]

    # The sibling's Home is their own.
    assert _home(sibling) == ["yt_2", "yt_1", "yt_0"]


def test_put_on_home_from_the_player(app):
    kid, _ = _setup(app)
    kid.post("/home/remove", data={"video_id": "yt_2", "back": "watch"})
    page = kid.get("/watch/yt_2").text
    assert 'action="/home/add"' in page and "Put on my Home" in page
    resp = kid.post("/home/add", data={"video_id": "yt_2", "back": "watch"})
    assert resp.headers["location"] == "/watch/yt_2?home=added"
    assert "on your Home now" in kid.get(resp.headers["location"]).text
    assert _home(kid) == ["yt_2", "yt_1", "yt_0"]
    assert 'action="/home/remove"' in kid.get("/watch/yt_2").text


def test_a_full_home_says_so(app):
    kid, _ = _setup(app, n=SLOTS + 1)
    for i in range(SLOTS):
        kid.post("/home/add", data={"video_id": f"yt_{i}", "slot": str(i)})
    resp = kid.post("/home/add", data={"video_id": f"yt_{SLOTS}"})
    assert resp.headers["location"] == f"/watch/yt_{SLOTS}?home=full"
    assert "Your Home is full" in kid.get(resp.headers["location"]).text


def test_a_hidden_video_leaves_its_spot_empty(app):
    kid, _ = _setup(app)
    kid.post("/home/move", data={"source": "0", "target": "4"})
    kid.post("/video/yt_1/hide")
    assert _home(kid) == [None, None, "yt_0", None, "yt_2"]
    # A video that isn't the kid's to see can't be put there.
    kid.post("/home/add", data={"video_id": "yt_1", "slot": "0"})
    kid.post("/home/add", data={"video_id": "nope", "slot": "1"})
    assert _home(kid) == [None, None, "yt_0", None, "yt_2"]


def test_all_and_channels_keep_working(app):
    kid, _ = _setup(app)
    kid.post("/home/remove", data={"video_id": "yt_1"})
    every = kid.get("/all").text
    assert all(f"/watch/yt_{i}" in every for i in range(3))
    assert 'href="/all" class="channel-link active"' in every
    # The Home button goes back to the kid's own Home.
    assert 'href="/"' in kid.get(re.search(r'href="(/channel/\d+)"', every).group(1)).text


def test_empty_home_after_taking_everything_off(app):
    kid, _ = _setup(app, n=1)
    kid.post("/home/remove", data={"video_id": "yt_0"})
    page = kid.get("/").text
    assert "Your Home is empty" in page
    assert 'href="/channels"' in page


def test_strangers_and_parents(app, client, authed_client):
    _setup(app)
    assert client.get("/all").headers["location"] == "/profiles"
    assert client.post("/home/add", data={"video_id": "yt_0"}).status_code in (302, 303)
    # A parent looking at the kid pages sees every video.
    assert "/watch/yt_0" in authed_client.get("/").text
