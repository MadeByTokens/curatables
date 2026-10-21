"""A kid picks the order of a channel's videos: newest, oldest, A to Z or
Z to A by the titles that kid sees."""

import re

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.repositories.override_repo import ProfileVideoOverrideRepository
from app.repositories.profile_channel_video_repo import ProfileChannelVideoRepository
from app.services.channel_order import title_key


def _conn(app):
    return next(app.dependency_overrides[get_db]())


def _video(conn, video_id, channel_id, title, minutes_ago):
    VideoRepository(conn).insert(Video(video_id=video_id, title=title, original_title=title,
                                       channel_id=channel_id, status="active", download_status="ready"))
    conn.execute("UPDATE videos SET added_at = datetime('now', ?) WHERE video_id = ?",
                 (f"-{minutes_ago} minutes", video_id))
    conn.commit()


def _kid(app, name):
    pid = ProfileRepository(_conn(app)).create(Profile(name=name, display_name=name.title()))
    client = TestClient(app, follow_redirects=False)
    client.post("/profiles/select", data={"profile_id": pid})
    return pid, client


def _order(client, channel_id, page=1):
    html = client.get(f"/channel/{channel_id}?page={page}").text
    return list(dict.fromkeys(re.findall(r'href="/watch/([\w-]+)"', html)))


def _trains(app):
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Trains")
    _video(conn, "yt_b", ch, "Blue train", 3)
    _video(conn, "yt_a", ch, "Árvore train", 1)
    _video(conn, "yt_c", ch, "cargo train", 2)
    return conn, ch


def test_titles_sort_as_people_read_them():
    titles = ["10 Cars", "2 Boats", "Zebra", "écluse", "Apple", "1 Train"]
    assert sorted(titles, key=title_key) == ["1 Train", "2 Boats", "10 Cars", "Apple", "écluse", "Zebra"]


def test_a_channel_starts_newest_first(app):
    _conn_, ch = _trains(app)
    _pid, kid = _kid(app, "enzo")
    assert _order(kid, ch) == ["yt_a", "yt_c", "yt_b"]
    page = kid.get(f"/channel/{ch}").text
    assert re.search(r'value="newest" class="sort-btn tappable active" aria-describedby="sort-label" aria-pressed="true"', page)


def test_the_kid_picks_an_order_and_it_stays(app):
    _conn_, ch = _trains(app)
    _pid, kid = _kid(app, "enzo")
    resp = kid.post(f"/channel/{ch}/sort", data={"sort": "az"})
    assert resp.status_code == 303 and resp.headers["location"] == f"/channel/{ch}"
    assert _order(kid, ch) == ["yt_a", "yt_b", "yt_c"]
    kid.post(f"/channel/{ch}/sort", data={"sort": "za"})
    assert _order(kid, ch) == ["yt_c", "yt_b", "yt_a"]
    kid.post(f"/channel/{ch}/sort", data={"sort": "oldest"})
    assert _order(kid, ch) == ["yt_b", "yt_c", "yt_a"]
    # The same tap again changes nothing; a made-up order is ignored.
    kid.post(f"/channel/{ch}/sort", data={"sort": "oldest"})
    kid.post(f"/channel/{ch}/sort", data={"sort": "random"})
    assert _order(kid, ch) == ["yt_b", "yt_c", "yt_a"]
    kid.post(f"/channel/{ch}/sort", data={"sort": "newest"})
    assert _order(kid, ch) == ["yt_a", "yt_c", "yt_b"]


def test_a_to_z_uses_the_kids_own_titles(app):
    """Enrico numbers his titles to put videos where he wants them."""
    conn, ch = _trains(app)
    pid, kid = _kid(app, "enzo")
    overrides = ProfileVideoOverrideRepository(conn)
    overrides.upsert(pid, "yt_c", title="1 Cargo")
    overrides.upsert(pid, "yt_b", title="10 Blue")
    overrides.upsert(pid, "yt_a", title="2 Tree")
    kid.post(f"/channel/{ch}/sort", data={"sort": "az"})
    assert _order(kid, ch) == ["yt_c", "yt_a", "yt_b"]


def test_each_kid_and_each_channel_keep_their_own_order(app):
    conn, ch = _trains(app)
    boats = ChannelRepository(conn).create("Boats")
    _video(conn, "yt_x", boats, "Xebec", 2)
    _video(conn, "yt_y", boats, "Yacht", 1)
    _pid, enzo = _kid(app, "enzo")
    _sid, sis = _kid(app, "lia")
    enzo.post(f"/channel/{ch}/sort", data={"sort": "az"})
    assert _order(enzo, boats) == ["yt_y", "yt_x"]
    assert _order(sis, ch) == ["yt_a", "yt_c", "yt_b"]


def test_pages_follow_the_order(app):
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Numbers")
    for i in range(30):
        _video(conn, f"yt_{i:02d}", ch, f"{i} clip", i)
    _pid, kid = _kid(app, "enzo")
    kid.post(f"/channel/{ch}/sort", data={"sort": "az"})
    assert _order(kid, ch) == [f"yt_{i:02d}" for i in range(24)]
    assert _order(kid, ch, page=2) == [f"yt_{i:02d}" for i in range(24, 30)]


def test_videos_saved_into_the_kids_own_channel_are_sorted_with_the_rest(app):
    conn, trains = _trains(app)
    pid, kid = _kid(app, "enzo")
    mine = ChannelRepository(conn).create("Enzo's best", owner_profile_id=pid)
    _video(conn, "yt_m", mine, "Monorail", 5)
    ProfileChannelVideoRepository(conn).add(pid, mine, "yt_b")
    kid.post(f"/channel/{mine}/sort", data={"sort": "az"})
    assert _order(kid, mine) == ["yt_b", "yt_m"]
    kid.post(f"/channel/{mine}/sort", data={"sort": "za"})
    assert _order(kid, mine) == ["yt_m", "yt_b"]


def test_an_order_for_a_channel_the_kid_cant_see_is_not_kept(app):
    conn, ch = _trains(app)
    pid = ProfileRepository(conn).create(Profile(name="ana", display_name="Ana",
                                                 allowed_channel_ids=[], channels_restricted=True))
    kid = TestClient(app, follow_redirects=False)
    kid.post("/profiles/select", data={"profile_id": pid})
    assert kid.post(f"/channel/{ch}/sort", data={"sort": "az"}).status_code == 303
    assert kid.post("/channel/9999/sort", data={"sort": "az"}).status_code == 303
    assert conn.execute("SELECT COUNT(*) FROM profile_channel_sort").fetchone()[0] == 0


def test_parents_see_no_order_buttons(app, authed_client):
    _conn_, ch = _trains(app)
    assert 'action="/channel/' not in authed_client.get(f"/channel/{ch}").text
    assert authed_client.post(f"/channel/{ch}/sort", data={"sort": "az"}).status_code in (302, 303, 403)
