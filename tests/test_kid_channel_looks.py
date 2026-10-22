"""The Channels page, and a kid's own name and picture for a channel:
seen only by that kid, everywhere the kid sees the channel."""

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository

PNG = b"\x89PNG\r\n\x1a\nPNG"


def _setup(app):
    conn = next(app.dependency_overrides[get_db]())
    channels = ChannelRepository(conn)
    trains = channels.create("Trains")
    VideoRepository(conn).insert(Video(video_id="yt_t", title="Steam", original_title="Steam",
                                       channel_id=trains, status="active", download_status="ready"))
    kids = []
    for name in ("enzo", "lia"):
        pid = ProfileRepository(conn).create(Profile(name=name, display_name=name.title()))
        client = TestClient(app, follow_redirects=False)
        client.post("/profiles/select", data={"profile_id": pid})
        kids.append((pid, client))
    return conn, trains, kids


def test_a_kid_renames_and_pictures_a_channel_for_themselves(app, authed_client):
    conn, trains, [(enzo_id, enzo), (_lia_id, lia)] = _setup(app)
    assert f'href="/channels/{trains}/look"' in enzo.get("/channels").text
    resp = enzo.post(f"/channels/{trains}/look", data={"name": "Choo choo", "emoji": "\U0001f682"})
    assert resp.status_code == 303 and resp.headers["location"] == "/channels"
    page = enzo.get("/channels").text
    assert "Choo choo" in page and "\U0001f682" in page
    channel_page = enzo.get(f"/channel/{trains}").text
    assert "Choo choo" in channel_page
    # Nobody else sees it.
    assert "Choo choo" not in lia.get("/channels").text and "Trains" in lia.get("/channels").text
    assert "Choo choo" not in authed_client.get("/parent/channels").text
    assert ChannelRepository(conn).get(trains).name == "Trains"
    # Back to the original.
    enzo.post(f"/channels/{trains}/look/reset")
    assert "Choo choo" not in enzo.get("/channels").text


def test_a_photo_is_shown_only_to_the_kid_who_chose_it(app):
    conn, trains, [(enzo_id, enzo), (_lia_id, lia)] = _setup(app)
    enzo.post(f"/channels/{trains}/look", data={"name": "Trains"},
              files={"picture": ("me.png", PNG, "image/png")})
    assert f'/media/channel/{trains}/my-icon' in enzo.get("/channels").text
    assert enzo.get(f"/media/channel/{trains}/my-icon").content == PNG
    assert lia.get(f"/media/channel/{trains}/my-icon").status_code == 404
    # The same name as the channel's is no change at all.
    assert ChannelRepository(conn).look(enzo_id, trains)["name"] == ""
    # A picture tapped later replaces the photo.
    enzo.post(f"/channels/{trains}/look", data={"name": "Trains", "emoji": "⭐"})
    look = ChannelRepository(conn).look(enzo_id, trains)
    assert look["emoji"] == "⭐" and look["picture"] == ""
    assert enzo.get(f"/media/channel/{trains}/my-icon").status_code == 404


def test_something_that_is_not_a_picture_is_refused_kindly(app):
    conn, trains, [(enzo_id, enzo), _] = _setup(app)
    resp = enzo.post(f"/channels/{trains}/look", data={"name": "X"},
                     files={"picture": ("notes.txt", b"hello", "text/plain")})
    assert resp.status_code == 400 and "That picture didn&#39;t work" in resp.text
    assert ChannelRepository(conn).look(enzo_id, trains)["name"] == ""


def test_only_channels_the_kid_sees_and_did_not_make(app):
    conn, trains, [(enzo_id, enzo), _] = _setup(app)
    secret = ChannelRepository(conn).create("Grown-up stuff")
    ProfileRepository(conn).update(enzo_id, allowed_channel_ids=[trains], channels_restricted=True)
    mine = ChannelRepository(conn).create("Enzo's", owner_profile_id=enzo_id)
    page = enzo.get("/channels").text
    assert f'href="/channel/{mine}/edit"' in page  # his own: the full Edit Channel
    for cid in (secret, mine, 9999):
        assert enzo.get(f"/channels/{cid}/look").status_code == 302
        enzo.post(f"/channels/{cid}/look", data={"name": "Mine now", "emoji": "⭐"})
    assert conn.execute("SELECT COUNT(*) FROM profile_channel_looks").fetchone()[0] == 0
    # A made-up picture is ignored.
    enzo.post(f"/channels/{trains}/look", data={"name": "Trains", "emoji": "<script>"})
    assert ChannelRepository(conn).look(enzo_id, trains)["emoji"] == ""


def test_without_customizing_there_is_nothing_to_change(app):
    conn, trains, [(enzo_id, enzo), _] = _setup(app)
    conn.execute("UPDATE profiles SET allow_customize = 0 WHERE id = ?", (enzo_id,))
    conn.commit()
    assert "/look" not in enzo.get("/channels").text
    assert enzo.get(f"/channels/{trains}/look").status_code != 200


def test_a_kid_makes_a_channel_from_the_channels_page(app):
    conn, trains, [(enzo_id, enzo), (_lia_id, lia)] = _setup(app)
    assert 'href="/channels/new"' in enzo.get("/channels").text
    assert enzo.get("/channels/new").status_code == 200
    resp = enzo.post("/channels/new", data={"name": "  My   drawings "})
    assert resp.status_code == 303 and resp.headers["location"] == "/channels"
    mine = ChannelRepository(conn).get_by_name("My drawings")
    assert mine is not None and mine.owner_profile_id == enzo_id
    page = enzo.get("/channels").text
    assert "My drawings" in page and f'href="/channel/{mine.id}/edit"' in page
    assert "My drawings" not in lia.get("/channels").text
    for name, says in (("Trains", "already used"), ("   ", "name is required")):
        resp = enzo.post("/channels/new", data={"name": name})
        assert resp.status_code == 400 and says in resp.text, name


def test_no_new_channels_without_customizing(app):
    conn, _trains, [(enzo_id, enzo), _] = _setup(app)
    conn.execute("UPDATE profiles SET allow_customize = 0 WHERE id = ?", (enzo_id,))
    conn.commit()
    assert 'href="/channels/new"' not in enzo.get("/channels").text
    enzo.post("/channels/new", data={"name": "Sneaky"})
    assert ChannelRepository(conn).get_by_name("Sneaky") is None
