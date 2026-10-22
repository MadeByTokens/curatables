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


def test_newest_and_oldest_go_by_the_last_download(app):
    """A video whose files were freed keeps its place until it downloads
    again; then it is the newest one (Enrico's order stays as it was
    until something really was downloaded again)."""
    conn, ch = _trains(app)
    _pid, kid = _kid(app, "enzo")
    videos = VideoRepository(conn)
    videos.mark_evicted("yt_b")
    assert _order(kid, ch) == ["yt_a", "yt_c", "yt_b"]
    assert videos.claim_evicted("yt_b")
    videos.update("yt_b", download_status="ready")
    assert _order(kid, ch) == ["yt_b", "yt_a", "yt_c"]
    kid.post(f"/channel/{ch}/sort", data={"sort": "oldest"})
    assert _order(kid, ch) == ["yt_c", "yt_a", "yt_b"]
    kid.post("/all/sort", data={"sort": "oldest"})
    assert _all(kid) == ["yt_c", "yt_a", "yt_b"]


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


def _all(client):
    html = client.get("/all").text
    return list(dict.fromkeys(re.findall(r'href="/watch/([\w-]+)"', html)))


def test_all_videos_has_its_own_order(app):
    """All videos takes the same order buttons, across every channel, by
    the kid's own titles, kept apart from each channel's order."""
    conn, trains = _trains(app)
    boats = ChannelRepository(conn).create("Boats")
    _video(conn, "yt_x", boats, "Xebec", 4)
    pid, kid = _kid(app, "enzo")
    assert _all(kid) == ["yt_a", "yt_c", "yt_b", "yt_x"]
    assert 'action="/all/sort"' in kid.get("/all").text
    resp = kid.post("/all/sort", data={"sort": "oldest"})
    assert resp.status_code == 303 and resp.headers["location"] == "/all"
    assert _all(kid) == ["yt_x", "yt_b", "yt_c", "yt_a"]
    ProfileVideoOverrideRepository(conn).upsert(pid, "yt_x", title="1 Boat")
    kid.post("/all/sort", data={"sort": "az"})
    assert _all(kid) == ["yt_x", "yt_a", "yt_b", "yt_c"]
    # The channel keeps its own order; another kid keeps newest first.
    assert _order(kid, trains) == ["yt_a", "yt_c", "yt_b"]
    _sid, sis = _kid(app, "lia")
    assert _all(sis) == ["yt_a", "yt_c", "yt_b", "yt_x"]
    kid.post("/all/sort", data={"sort": "newest"})
    assert _all(kid) == ["yt_a", "yt_c", "yt_b", "yt_x"]


def test_parents_get_no_order_buttons_on_all_videos(app, authed_client):
    _trains(app)
    assert 'action="/all/sort"' not in authed_client.get("/all").text


def test_came_out_first_goes_by_when_a_video_came_out(app):
    """Newest and Oldest go by when a video was downloaded; Came out first
    lines videos up by YouTube's upload date, whatever order they were
    downloaded in; one with no date (an upload) counts from the day it
    was added."""
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Series")
    videos = VideoRepository(conn)
    for vid, made, minutes_ago in (("yt_ep2", "20200301", 1), ("yt_ep1", "20200101", 2),
                                   ("yt_ep3", "20200601", 3), ("up_mine", "", 4)):
        videos.insert(Video(video_id=vid, title=vid, original_title=vid, channel_id=ch, upload_date=made,
                            status="active", download_status="ready"))
        conn.execute("UPDATE videos SET added_at = datetime('now', ?) WHERE video_id = ?",
                     (f"-{minutes_ago} minutes", vid))
    conn.commit()
    _pid, kid = _kid(app, "enzo")
    assert _order(kid, ch) == ["yt_ep2", "yt_ep1", "yt_ep3", "up_mine"]
    kid.post(f"/channel/{ch}/sort", data={"sort": "oldest"})
    assert _order(kid, ch) == ["up_mine", "yt_ep3", "yt_ep1", "yt_ep2"]
    kid.post(f"/channel/{ch}/sort", data={"sort": "made"})
    assert _order(kid, ch) == ["yt_ep1", "yt_ep2", "yt_ep3", "up_mine"]
    kid.post("/all/sort", data={"sort": "made"})
    assert _all(kid) == ["yt_ep1", "yt_ep2", "yt_ep3", "up_mine"]
    assert 'value="mine"' not in kid.get("/all").text  # My order is for channels


def test_a_shows_episodes_stay_in_order(app):
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Show")
    videos = VideoRepository(conn)
    for vid, ep in (("e3", 1003), ("e1", 1001), ("e2", 1002)):
        videos.insert(Video(video_id=vid, title=vid, original_title=vid, channel_id=ch,
                            status="active", download_status="ready"))
        videos.update(vid, episode_key=ep)
    _pid, kid = _kid(app, "enzo")
    assert _order(kid, ch) == ["e1", "e2", "e3"]


# --- My order ---------------------------------------------------------------

def _five(app):
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Fives")
    for i in range(1, 6):
        _video(conn, f"v{i}", ch, f"Video {i}", 10 - i)  # v5 newest
    return conn, ch


def _draft(kid, ch):
    html = kid.get(f"/channel/{ch}/arrange").text
    return re.findall(r'id="row-([\w-]+)"', html)


def _do(kid, ch, **data):
    return kid.post(f"/channel/{ch}/arrange", data=data)


def test_picking_my_order_changes_nothing_by_itself(app):
    conn, ch = _five(app)
    pid, kid = _kid(app, "enzo")
    before = _order(kid, ch)
    kid.post(f"/channel/{ch}/sort", data={"sort": "mine"})
    page = kid.get(f"/channel/{ch}").text
    assert _order(kid, ch) == before == ["v5", "v4", "v3", "v2", "v1"]
    assert "Nothing saved yet" in page and f'href="/channel/{ch}/arrange"' in page
    # Opening the arranging page doesn't change the channel either.
    assert _draft(kid, ch) == before
    assert _order(kid, ch) == before


def test_arranging_starts_from_the_order_the_kid_was_looking_at(app):
    conn, ch = _five(app)
    _pid, kid = _kid(app, "enzo")
    kid.post(f"/channel/{ch}/sort", data={"sort": "oldest"})
    assert _draft(kid, ch) == ["v1", "v2", "v3", "v4", "v5"]


def test_moves_undo_and_save(app):
    conn, ch = _five(app)
    _pid, kid = _kid(app, "enzo")
    assert _draft(kid, ch) == ["v5", "v4", "v3", "v2", "v1"]
    resp = _do(kid, ch, do="top", video_id="v1", page="1")
    assert resp.status_code == 303 and "moved=v1" in resp.headers["location"]
    assert _draft(kid, ch) == ["v1", "v5", "v4", "v3", "v2"]
    _do(kid, ch, do="top", video_id="v1", page="1")  # the same tap again: nothing to undo
    _do(kid, ch, do="down", video_id="v1", page="1")
    _do(kid, ch, do="end", video_id="v5", page="1")
    _do(kid, ch, do="place", video_id="v2", place="2", page="1")
    assert _draft(kid, ch) == ["v1", "v2", "v4", "v3", "v5"]
    _do(kid, ch, do="undo", page="1")
    assert _draft(kid, ch) == ["v1", "v4", "v3", "v2", "v5"]
    # The channel shows nothing of this until Save.
    assert _order(kid, ch) == ["v5", "v4", "v3", "v2", "v1"]
    resp = _do(kid, ch, do="save", page="1")
    assert resp.headers["location"] == f"/channel/{ch}?done=saved"
    page = kid.get(resp.headers["location"]).text
    assert "Saved!" in page and "Undo save" in page
    assert _order(kid, ch) == ["v1", "v4", "v3", "v2", "v5"]
    # Undo save: the order before, and the choice before.
    kid.post(f"/channel/{ch}/undo-save")
    assert _order(kid, ch) == ["v5", "v4", "v3", "v2", "v1"]
    assert 'value="newest" class="sort-btn tappable active"' in kid.get(f"/channel/{ch}").text


def test_starting_over_asks_first_once_videos_were_moved(app):
    conn, ch = _five(app)
    _pid, kid = _kid(app, "enzo")
    _draft(kid, ch)
    resp = _do(kid, ch, do="start", sort="az", page="1")  # nothing moved yet: no question
    assert "ask=" not in resp.headers["location"]
    assert _draft(kid, ch) == ["v1", "v2", "v3", "v4", "v5"]
    _do(kid, ch, do="top", video_id="v3", page="1")
    resp = _do(kid, ch, do="start", sort="newest", page="1")
    assert "ask=newest" in resp.headers["location"]
    asked = kid.get(resp.headers["location"]).text
    assert "Start over from Newest?" in asked
    assert _draft(kid, ch) == ["v3", "v1", "v2", "v4", "v5"]  # nothing wiped by the first tap
    _do(kid, ch, do="start", sort="newest", sure="1", page="1")
    assert _draft(kid, ch) == ["v5", "v4", "v3", "v2", "v1"]
    _do(kid, ch, do="undo", page="1")  # and even that can be undone
    assert _draft(kid, ch) == ["v3", "v1", "v2", "v4", "v5"]


def test_cancel_leaves_the_channel_as_it_was(app):
    conn, ch = _five(app)
    _pid, kid = _kid(app, "enzo")
    _do(kid, ch, do="top", video_id="v1", page="1")
    resp = _do(kid, ch, do="cancel", page="1")
    assert "Nothing was changed" in kid.get(resp.headers["location"]).text
    assert _order(kid, ch) == ["v5", "v4", "v3", "v2", "v1"]
    assert _draft(kid, ch) == ["v5", "v4", "v3", "v2", "v1"]  # a fresh draft


def test_a_saved_order_stays_when_videos_come_and_go(app):
    """A video added later goes in by when it came out, without moving any
    placed one; one deleted just drops out."""
    from app.models import Video
    conn, ch = _five(app)
    _pid, kid = _kid(app, "enzo")
    videos = VideoRepository(conn)
    for vid, made in (("v1", "20200101"), ("v2", "20200201"), ("v3", "20200301"),
                      ("v4", "20200401"), ("v5", "20200501")):
        conn.execute("UPDATE videos SET upload_date = ? WHERE video_id = ?", (made, vid))
    conn.commit()
    kid.post(f"/channel/{ch}/sort", data={"sort": "made"})
    _do(kid, ch, do="top", video_id="v5", page="1")
    _do(kid, ch, do="save", page="1")
    assert _order(kid, ch) == ["v5", "v1", "v2", "v3", "v4"]
    videos.insert(Video(video_id="new", title="New", original_title="New", channel_id=ch,
                        upload_date="20200215", status="active", download_status="ready"))
    videos.update("v3", status="hidden")
    assert _order(kid, ch) == ["v5", "v1", "v2", "new", "v4"]


def test_the_same_button_over_and_over_settles(app):
    conn, ch = _five(app)
    _pid, kid = _kid(app, "enzo")
    for _ in range(25):
        assert _do(kid, ch, do="down", video_id="v5", page="1").status_code == 303
    for _ in range(5):
        _do(kid, ch, do="place", video_id="v2", place="999", page="1")
        _do(kid, ch, do="place", video_id="v2", place="nope", page="1")
    order = _draft(kid, ch)
    assert sorted(order) == ["v1", "v2", "v3", "v4", "v5"] and order[-1] == "v2"


def test_my_order_is_each_kids_own(app, authed_client):
    conn, ch = _five(app)
    _pid, kid = _kid(app, "enzo")
    _sid, sis = _kid(app, "lia")
    _do(kid, ch, do="top", video_id="v1", page="1")
    _do(kid, ch, do="save", page="1")
    assert _order(sis, ch) == ["v5", "v4", "v3", "v2", "v1"]
    assert authed_client.get(f"/channel/{ch}/arrange").status_code in (302, 303)
    secret = ChannelRepository(conn).create("Grown-up")
    ProfileRepository(conn).update(_pid, allowed_channel_ids=[ch], channels_restricted=True)
    assert kid.get(f"/channel/{secret}/arrange").status_code == 302
