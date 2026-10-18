"""Kid wishlist: server-side YouTube search, filtered, approved by a parent."""
import re

import pytest

from app.backends.base import BackendError, BackendErrorType, ChannelHit, VideoMetadata
from app.dependencies import get_backend, get_db, get_thumbnail_service
from app.repositories import ProfileRepository, VideoRepository
from app.repositories.wishlist_repo import WishlistRepository
from app.services.profiles import ProfileService
from app.services.thumbnails import ThumbnailService
from app.services.search_gate import GATE, SearchGate, SearchRefused

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _meta(raw, title, channel="Kids TV", duration=120, live=False):
    return VideoMetadata(video_id=raw, title=title, channel=channel, duration=duration,
                         view_count=0, upload_date="", description="",
                         thumbnail_url=f"https://i.ytimg.com/vi/{raw}/hq.jpg",
                         extractor="youtube",
                         original_url=f"https://www.youtube.com/watch?v={raw}",
                         is_live=live)


RESULTS = [
    _meta("aaaaaaaaaaa", "Counting to ten"),
    _meta("bbbbbbbbbbb", "Scary monster night"),
    _meta("ccccccccccc", "Two hour lullabies", duration=7200),
    _meta("ddddddddddd", "Live zoo cam", live=True),
    _meta("eeeeeeeeeee", "Shapes song", channel="Other Channel"),
]


CHANNELS = [
    ChannelHit("UC" + "a" * 22, "Kids TV", "https://yt3.ggpht.com/a=s176"),
    ChannelHit("UC" + "b" * 22, "Scary Stories", "https://yt3.ggpht.com/b=s176"),
    ChannelHit("UC" + "c" * 22, "Art Club", "https://yt3.ggpht.com/c=s176"),
]


class FakeBackend:
    last_error = None

    def __init__(self):
        self.queries = []
        self.fail = None

    def _result(self, items):
        self.last_error = None
        if self.fail:
            self.last_error = BackendError(self.fail, "it broke", "details")
            return []
        return items

    def search(self, query, max_results=12, newest_first=False):
        self.queries.append((query, newest_first) if newest_first else query)
        return self._result(list(RESULTS))

    def search_channels(self, query, max_results=12):
        self.queries.append(("channels", query))
        return self._result(list(CHANNELS))

    def fetch_channel_videos(self, url, max_results=50):
        self.queries.append(("channel", url))
        cid = url.split("/channel/")[1].split("/")[0]
        name = next(c.name for c in CHANNELS if c.channel_id == cid)
        return name, self._result([m for m in RESULTS if m.channel == name] if name == "Kids TV"
                                  else [_meta("fffffffffff", "Spooky", channel=name)])

    def fetch_video_info(self, url):
        raw = url.rsplit("=", 1)[-1]
        return next((m for m in RESULTS if m.video_id == raw), None)


class FakeThumbnails(ThumbnailService):
    def download(self, video_id, thumbnail_url=None):
        path = self.get_path(video_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(JPEG)
        return path


class _Shared:
    """The test's connection for background work, which may not close it."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


@pytest.fixture
def backend(app):
    conn = next(app.dependency_overrides[get_db]())
    app.state.db_connect = lambda: _Shared(conn)
    fake = FakeBackend()
    app.dependency_overrides[get_backend] = lambda: fake
    app.dependency_overrides[get_thumbnail_service] = lambda: FakeThumbnails(app.state.config.data_dir)
    GATE.reset()
    yield fake
    app.dependency_overrides.pop(get_backend, None)
    app.dependency_overrides.pop(get_thumbnail_service, None)


def _conn(app):
    return next(app.dependency_overrides[get_db]())


def _kid(app, client, name="wren", wishlist=True, search_mode="disabled", **features):
    svc = ProfileService(ProfileRepository(_conn(app)))
    pid = svc.create(name=name, display_name=name.title(), allow_wishlist=wishlist,
                     search_mode=search_mode, **features)
    assert client.post("/profiles/select", data={"profile_id": pid}).status_code == 302
    return pid


def test_wishlist_is_off_unless_the_parent_switches_it_on(app, client, backend):
    _kid(app, client, wishlist=False)
    assert client.get("/wishlist").status_code == 302
    assert client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"}).status_code in (302, 403)
    assert backend.queries == []


def test_search_applies_the_parents_rules(app, client, backend):
    _kid(app, client)
    app.state.config.wishlist.max_duration_minutes = 30
    page = client.get("/wishlist", params={"q": "songs"}).text
    assert "Counting to ten" in page
    assert "Shapes song" in page
    assert "Scary monster" not in page
    assert "Two hour lullabies" not in page
    assert "Live zoo cam" not in page
    assert 'href="/wishlist"' in page


def test_allowlist_limits_results_to_those_channels(app, client, backend):
    app.state.config.wishlist.allowed_channels = ["kids tv"]
    try:
        _kid(app, client)
        page = client.get("/wishlist", params={"q": "songs"}).text
        assert "Counting to ten" in page and "Shapes song" not in page
    finally:
        app.state.config.wishlist.allowed_channels = []


def test_blocked_query_never_reaches_youtube(app, client, backend):
    _kid(app, client)
    page = client.get("/wishlist", params={"q": "horror movie"}).text
    assert "look for something else" in page
    assert backend.queries == []


def test_kid_asks_and_parent_sees_the_request(app, client, authed_client, backend):
    pid = _kid(app, client)
    client.get("/wishlist", params={"q": "songs"})
    resp = client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    assert resp.status_code == 303
    mine = client.get("/wishlist?asked=1").text
    assert "Asked!" in mine and "Waiting" in mine
    pending = WishlistRepository(_conn(app)).list_by_status("pending")
    assert [(w.profile_id, w.video_id) for w in pending] == [(pid, "youtube_aaaaaaaaaaa")]
    assert VideoRepository(_conn(app)).get("youtube_aaaaaaaaaaa") is None

    inbox = authed_client.get("/parent/requests").text
    assert "Counting to ten" in inbox and "Wren" in inbox
    assert "1 video request from your kids" in authed_client.get("/parent/").text


def test_kid_names_the_video_and_parent_reviews_without_youtube(app, client, authed_client, backend):
    from app.repositories.channel_repo import ChannelRepository
    channels = ChannelRepository(_conn(app))
    songs = channels.create("Songs")
    _kid(app, client)
    client.get("/wishlist", params={"q": "songs"})
    resp = client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa",
                                              "back": "/wishlist?q=songs&mode=videos"})
    assert resp.status_code == 303
    item = WishlistRepository(_conn(app)).list_by_status("pending")[0]
    assert resp.headers["location"].startswith(f"/wishlist/asked/{item.id}?back=")
    page = client.get(resp.headers["location"]).text
    assert 'value="Counting to ten"' in page and "Songs" in page

    resp = client.post(f"/wishlist/asked/{item.id}", data={
        "title": "  My   counting  song ", "description": "Numbers!",
        "channel_id": str(songs), "back": "/wishlist?q=songs&mode=videos"})
    assert resp.headers["location"] == f"/wishlist?q=songs&mode=videos&asked=1#ask-{item.raw_id}"
    assert f'id="ask-{item.raw_id}"' in client.get("/wishlist", params={"q": "songs"}).text
    item = WishlistRepository(_conn(app)).get(item.id)
    assert (item.title, item.description, item.target_channel_id) == (
        "My counting song", "Numbers!", songs)
    assert item.original_title == "Counting to ten"

    backend.queries.clear()
    calls = []
    backend.fetch_video_info = lambda url: calls.append(url)
    review = authed_client.get(f"/parent/requests/{item.id}/review").text
    assert calls == [] and backend.queries == []
    assert 'value="My counting song"' in review and ">Numbers!</textarea>" in review
    assert "This text comes from" not in review
    assert f'value="{songs}" selected' in review
    assert 'name="original_title_aaaaaaaaaaa" value="Counting to ten"' in review
    assert "On YouTube: Counting to ten" in authed_client.get("/parent/requests").text


def test_kid_takes_back_a_request_and_can_ask_again(app, client, backend):
    pid = _kid(app, client)
    client.get("/wishlist", params={"q": "songs"})
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    item = WishlistRepository(_conn(app)).list_by_status("pending")[0]
    assert "Oops, don&#39;t ask" in client.get(f"/wishlist/asked/{item.id}").text
    listed = client.get("/wishlist").text
    assert f'action="/wishlist/asked/{item.id}/cancel"' in listed

    _kid(app, client, "otto")
    client.post(f"/wishlist/asked/{item.id}/cancel")
    assert WishlistRepository(_conn(app)).get(item.id) is not None

    client.post("/profiles/select", data={"profile_id": pid})
    resp = client.post(f"/wishlist/asked/{item.id}/cancel",
                       data={"back": "/wishlist?q=songs&mode=videos"})
    assert resp.headers["location"] == f"/wishlist?q=songs&mode=videos#ask-{item.raw_id}"
    assert WishlistRepository(_conn(app)).get(item.id) is None
    page = client.get("/wishlist", params={"q": "songs"}).text
    assert "What I asked for" not in page
    assert 'name="raw_id" value="aaaaaaaaaaa"' in page
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    assert len(WishlistRepository(_conn(app)).list_by_status("pending")) == 1


def test_parent_removes_a_request_without_turning_it_down(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    client.post("/wishlist/ask", data={"raw_id": "eeeeeeeeeee"})
    repo = WishlistRepository(_conn(app))
    first, second = repo.list_by_status("pending")
    resp = authed_client.post(f"/parent/requests/{first.id}/remove")
    assert resp.headers["location"] == "/parent/requests?notice=removed"
    assert repo.get(first.id).status == "removed"

    authed_client.post(f"/parent/requests/{second.id}/reject")
    assert "Let Wren ask again" in authed_client.get("/parent/requests").text
    authed_client.post(f"/parent/requests/{second.id}/remove")
    assert repo.get(second.id).status == "allowed_again"
    page = client.get("/wishlist", params={"q": "songs"}).text
    assert 'name="raw_id" value="aaaaaaaaaaa"' in page
    assert 'name="raw_id" value="eeeeeeeeeee"' in page
    assert "What I asked for" not in page


def test_a_removed_request_can_be_put_back(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    client.post("/wishlist/ask", data={"raw_id": "eeeeeeeeeee"})
    repo = WishlistRepository(_conn(app))
    first, second = repo.list_by_status("pending")
    client.post(f"/wishlist/asked/{first.id}", data={"title": "My counting", "description": "Numbers!"})
    authed_client.post(f"/parent/requests/{first.id}/remove")
    authed_client.post(f"/parent/requests/{second.id}/reject")
    authed_client.post(f"/parent/requests/{second.id}/remove")
    decided = authed_client.get("/parent/requests").text.split("Recently decided")[1]
    assert "Removed" in decided and "Allowed to ask again" in decided
    assert decided.count("/put-back") == 2

    resp = authed_client.post(f"/parent/requests/{first.id}/put-back")
    assert resp.headers["location"] == "/parent/requests?notice=put-back"
    item = repo.get(first.id)
    assert (item.status, item.title, item.description) == ("pending", "My counting", "Numbers!")
    authed_client.post(f"/parent/requests/{second.id}/put-back")
    assert repo.get(second.id).status == "rejected"
    assert "Not this time" in client.get("/wishlist").text
    authed_client.post(f"/parent/requests/{first.id}/put-back")
    assert repo.get(first.id).status == "pending"


def test_the_kid_asking_again_reuses_a_removed_request(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    repo = WishlistRepository(_conn(app))
    item = repo.list_by_status("pending")[0]
    authed_client.post(f"/parent/requests/{item.id}/remove")
    resp = client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    assert resp.headers["location"].startswith(f"/wishlist/asked/{item.id}?")
    assert repo.get(item.id).status == "pending"
    assert len(repo.list_for_profile(item.profile_id)) == 1


def test_kid_makes_a_channel_for_the_video(app, client, authed_client, backend):
    pid = _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    repo = WishlistRepository(_conn(app))
    item = repo.list_by_status("pending")[0]
    client.post(f"/wishlist/asked/{item.id}", data={"new_channel": "  My   Songs "})
    from app.repositories.channel_repo import ChannelRepository
    channel = ChannelRepository(_conn(app)).get_by_name("My Songs")
    assert channel is not None and channel.owner_profile_id == pid
    assert repo.get(item.id).target_channel_id == channel.id
    assert "<strong>My Songs</strong>" in authed_client.get("/parent/requests").text
    review = authed_client.get(f"/parent/requests/{item.id}/review").text
    assert f'value="{channel.id}" selected' in review

    # The same name again picks that channel instead of failing.
    client.post(f"/wishlist/asked/{item.id}", data={"new_channel": "my songs"})
    assert repo.get(item.id).target_channel_id == channel.id

    otto = ProfileService(ProfileRepository(_conn(app))).create(name="otto")
    ChannelRepository(_conn(app)).create("Taken", owner_profile_id=otto)
    client.post("/wishlist/ask", data={"raw_id": "eeeeeeeeeee"})
    other = [w for w in repo.list_by_status("pending") if w.id != item.id][0]
    resp = client.post(f"/wishlist/asked/{other.id}", data={"new_channel": "Taken",
                                                            "title": "Shapes!"})
    assert resp.status_code == 400 and "already used" in resp.text
    assert 'value="Taken"' in resp.text
    assert repo.get(other.id).title == "Shapes!"


def test_kid_without_channels_of_their_own_suggests_one(app, client, authed_client, backend):
    _kid(app, client, allow_customize=False)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    repo = WishlistRepository(_conn(app))
    item = repo.list_by_status("pending")[0]
    assert "Or ask for a new channel" in client.get(f"/wishlist/asked/{item.id}").text
    client.post(f"/wishlist/asked/{item.id}", data={"new_channel": "Counting"})
    from app.repositories.channel_repo import ChannelRepository
    assert ChannelRepository(_conn(app)).get_by_name("Counting") is None
    item = repo.get(item.id)
    assert (item.target_channel_id, item.suggested_channel) == (None, "Counting")
    assert "a new channel, <strong>Counting</strong>" in authed_client.get("/parent/requests").text
    review = authed_client.get(f"/parent/requests/{item.id}/review").text
    assert 'name="new_channel_name" placeholder="New channel name..."\n             value="Counting"' in review


def test_kid_choices_are_checked(app, client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa", "back": "https://evil.example/"})
    item = WishlistRepository(_conn(app)).list_by_status("pending")[0]
    resp = client.post(f"/wishlist/asked/{item.id}", data={
        "title": "", "description": "x" * 5000, "channel_id": "99999",
        "back": "//evil.example/"})
    assert resp.headers["location"] == f"/wishlist?asked=1#ask-{item.raw_id}"
    item = WishlistRepository(_conn(app)).get(item.id)
    assert item.title == "Counting to ten" and len(item.description) == 1000
    assert item.target_channel_id is None

    _kid(app, client, "otto")
    assert client.get(f"/wishlist/asked/{item.id}").headers["location"] == "/wishlist"
    client.post(f"/wishlist/asked/{item.id}", data={"title": "Mine now"})
    assert WishlistRepository(_conn(app)).get(item.id).title == "Counting to ten"


def test_a_second_kid_asking_reuses_the_stored_details(app, client, backend):
    _kid(app, client, "wren")
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    GATE.reset()
    calls = []
    backend.fetch_video_info = lambda url: calls.append(url)
    _kid(app, client, "otto")
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    assert calls == []
    assert len(WishlistRepository(_conn(app)).list_by_status("pending")) == 2


def test_ask_rechecks_the_video_on_the_server(app, client, backend):
    _kid(app, client)
    for raw in ("bbbbbbbbbbb", "zzzzzzzzzzz", "../../etc"):
        page = client.post("/wishlist/ask", data={"raw_id": raw}).text
        assert "Waiting" not in page
    assert WishlistRepository(_conn(app)).list_by_status("pending") == []


def test_thumbnails_only_for_what_the_kid_was_shown(app, client, backend):
    _kid(app, client)
    assert client.get("/wishlist/thumb/youtube_aaaaaaaaaaa").status_code == 404
    client.get("/wishlist", params={"q": "songs"})
    resp = client.get("/wishlist/thumb/youtube_aaaaaaaaaaa")
    assert resp.status_code == 200 and resp.headers["content-type"] == "image/jpeg"
    assert client.get("/wishlist/thumb/youtube_bbbbbbbbbbb").status_code == 404


def test_rejected_video_is_hidden_from_that_kid(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    item = WishlistRepository(_conn(app)).list_by_status("pending")[0]
    assert authed_client.post(f"/parent/requests/{item.id}/reject").status_code == 303
    decided = authed_client.get("/parent/requests").text.split("Recently decided")[1]
    assert f'href="{item.url}"' in decided and "/parent/content/" not in decided
    page = client.get("/wishlist", params={"q": "songs"}).text
    assert "Counting to ten</span>" not in page.split("What I asked for")[0]
    assert "Not this time" in page
    again = client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"}).text
    assert "not this one" in again


def test_recently_decided_shows_the_latest_decisions(app, client, authed_client, backend):
    pid = _kid(app, client)
    conn = _conn(app)
    repo = WishlistRepository(conn)
    for i in range(30):
        raw = f"v{i:010d}"
        rid = repo.add(pid, f"youtube_{raw}", _meta(raw, f"Video {i}"), f"https://example.com/{raw}")
        repo.decide(rid, "approved" if i % 2 else "rejected")
        conn.execute("UPDATE wishlist_requests SET decided_at = datetime('now', ?) WHERE id = ?",
                     (f"-{30 - i} minutes", rid))
    conn.commit()
    decided = authed_client.get("/parent/requests").text.split("Recently decided")[1]
    shown = re.findall(r"Video (\d+)\b", decided)
    assert shown == [str(i) for i in range(29, 9, -1)]


def test_more_than_a_page_of_requests_says_how_many(app, client, authed_client, backend):
    pid = _kid(app, client)
    repo = WishlistRepository(_conn(app))
    for i in range(105):
        raw = f"v{i:010d}"
        repo.add(pid, f"youtube_{raw}", _meta(raw, f"Video {i}"), f"https://example.com/{raw}")
    page = authed_client.get("/parent/requests").text
    assert "Showing the oldest 100 of 105 requests." in page
    assert page.count("/review\"") == 100


def test_adding_the_video_approves_the_request(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    from unittest.mock import patch
    with patch("app.services.content.ContentService.add_video", return_value=object()):
        resp = authed_client.post("/parent/add/confirm", data={
            "url_type": "video", "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            "source_title": "Counting to ten", "video_ids": "aaaaaaaaaaa",
            "extractor_aaaaaaaaaaa": "youtube", "watched_aaaaaaaaaaa": "1",
            "channel_id": "none", "from_request": "1",
        })
    # Back to the list, for the next request.
    assert resp.status_code == 303
    assert resp.headers["location"] == "/parent/requests?notice=added"
    assert "It is downloading now" in authed_client.get(resp.headers["location"]).text
    repo = WishlistRepository(_conn(app))
    assert repo.list_by_status("pending") == []
    assert repo.list_by_status("approved")[0].video_id == "youtube_aaaaaaaaaaa"


def _full_details(backend, description=""):
    """YouTube's full lookup: it always carries an upload date."""
    from dataclasses import replace
    plain = backend.fetch_video_info
    backend.fetch_video_info = lambda url: (
        lambda m: m and replace(m, upload_date="20240101", description=description))(plain(url))


def _auto_kid(app, client, **kw):
    from app.repositories.channel_repo import ChannelRepository
    into = ChannelRepository(_conn(app)).create("From YouTube")
    return _kid(app, client, auto_add_channel_id=into, **kw), into


def test_requests_from_allowed_channels_are_added_straight_away(app, client, authed_client, backend):
    from unittest.mock import patch
    app.state.config.wishlist.allowed_channels = ["Kids TV"]
    _full_details(backend)
    pid, into = _auto_kid(app, client)
    from app.repositories.channel_repo import ChannelRepository
    own = ChannelRepository(_conn(app)).create("Wren's", owner_profile_id=pid)
    client.get("/wishlist", params={"q": "songs"})
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    repo = WishlistRepository(_conn(app))
    item = repo.list_by_status("pending")[0]
    with patch("app.services.content.ContentService._start_download") as download:
        resp = client.post(f"/wishlist/asked/{item.id}", data={
            "title": "My counting", "description": "Numbers!", "channel_id": str(own)})
    assert resp.headers["location"] == f"/wishlist?asked=auto#ask-{item.raw_id}"
    assert download.call_count == 1
    video = VideoRepository(_conn(app)).get("youtube_aaaaaaaaaaa")
    assert (video.channel_id, video.title, video.description) == (into, "My counting", "Numbers!")
    item = repo.get(item.id)
    assert item.status == "approved" and item.auto_added
    assert "It is on its way" in client.get(resp.headers["location"]).text
    from app.repositories.profile_channel_video_repo import ProfileChannelVideoRepository
    assert ProfileChannelVideoRepository(_conn(app)).channels_for_video(pid, item.video_id) == [own]
    assert "Added automatically" in authed_client.get("/parent/requests").text


def test_parent_takes_back_an_auto_added_video(app, client, authed_client, backend):
    from unittest.mock import patch
    app.state.config.wishlist.allowed_channels = ["Kids TV"]
    _full_details(backend)
    pid, into = _auto_kid(app, client)
    repo = WishlistRepository(_conn(app))
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    item = repo.list_by_status("pending")[0]
    other = _kid(app, client, "otto")
    other_id = repo.add(other, item.video_id, item.metadata(), item.url)
    with patch("app.services.content.ContentService._start_download"):
        client.post("/profiles/select", data={"profile_id": pid})
        client.post(f"/wishlist/asked/{item.id}", data={"title": ""})
    assert repo.get(other_id).status == "approved"
    page = authed_client.get("/parent/requests").text
    assert f'action="/parent/requests/{item.id}/undo-auto"' in page
    assert f'action="/parent/requests/{other_id}/undo-auto"' not in page

    resp = authed_client.post(f"/parent/requests/{item.id}/undo-auto")
    assert resp.headers["location"] == "/parent/requests?notice=undone"
    assert VideoRepository(_conn(app)).get(item.video_id) is None
    assert repo.get(item.id).status == "rejected"
    assert repo.get(other_id).status == "pending"
    authed_client.post(f"/parent/requests/{other_id}/undo-auto")
    assert repo.get(other_id).status == "pending"


def test_requests_wait_without_an_allowed_list_or_with_a_blocked_word(app, client, backend):
    from unittest.mock import patch
    _full_details(backend, description="a big spider")
    _auto_kid(app, client)
    repo = WishlistRepository(_conn(app))
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    item = repo.list_by_status("pending")[0]
    with patch("app.services.content.ContentService.add_video") as add:
        resp = client.post(f"/wishlist/asked/{item.id}", data={"title": ""})
        assert resp.headers["location"].endswith("asked=1#ask-aaaaaaaaaaa")
        assert repo.get(item.id).status == "pending"

        app.state.config.wishlist.allowed_channels = ["Kids TV"]
        app.state.config.wishlist.blocked_words = ["spider"]
        client.post(f"/wishlist/asked/{item.id}", data={"title": ""})
        assert repo.get(item.id).status == "pending"
        conn = _conn(app)
        conn.execute("UPDATE wishlist_requests SET upload_date = '' WHERE id = ?", (item.id,))
        conn.commit()
        client.post(f"/wishlist/asked/{item.id}", data={"title": ""})
    assert add.call_count == 0
    assert repo.get(item.id).status == "rejected"


def test_parent_sets_where_requests_go(app, authed_client):
    from app.repositories.channel_repo import ChannelRepository
    channels = ChannelRepository(_conn(app))
    into = channels.create("From YouTube")
    pid = ProfileService(ProfileRepository(_conn(app))).create(name="ivy", display_name="Ivy")
    kids = channels.create("Ivy's", owner_profile_id=pid)
    form = authed_client.get(f"/parent/profiles/{pid}/edit").text
    assert f'<option value="{into}">Yes, into From YouTube</option>' in form
    assert f'value="{kids}">Yes, into' not in form
    assert "Your allowed list is empty" in form
    authed_client.post(f"/parent/profiles/{pid}/edit", data={
        "display_name": "Ivy", "auto_add_channel_id": str(into)})
    assert ProfileRepository(_conn(app)).get(pid).auto_add_channel_id == into
    authed_client.post(f"/parent/profiles/{pid}/edit", data={
        "display_name": "Ivy", "auto_add_channel_id": str(kids)})
    assert ProfileRepository(_conn(app)).get(pid).auto_add_channel_id is None


def test_adding_needs_a_channel_chosen_on_purpose(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    form = {
        "url_type": "video", "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
        "source_title": "Counting to ten", "video_ids": "aaaaaaaaaaa",
        "extractor_aaaaaaaaaaa": "youtube", "watched_aaaaaaaaaaa": "1", "channel_id": "",
    }
    resp = authed_client.post("/parent/add/confirm", data=form)
    assert resp.status_code == 400 and "Pick a channel" in resp.text
    assert WishlistRepository(_conn(app)).list_by_status("approved") == []
    review = authed_client.get(f"/parent/requests/{WishlistRepository(_conn(app)).list_by_status('pending')[0].id}/review").text
    assert '<option value="">Choose a channel' in review and 'name="from_request"' in review


def test_other_kids_cannot_see_a_siblings_requests(app, client, backend):
    _kid(app, client, "wren")
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    _kid(app, client, "otto")
    page = client.get("/wishlist").text
    assert "Counting to ten" not in page
    assert client.get("/wishlist/thumb/youtube_aaaaaaaaaaa").status_code == 404


def test_parent_edits_the_rules(app, authed_client):
    resp = authed_client.post("/parent/settings/wishlist", data={
        "blocked_words": "spiders, sharks\nspiders", "allowed_channels": "Kids TV\n",
        "max_duration_minutes": "15", "max_results": "6"})
    assert resp.status_code == 200
    rules = app.state.config.wishlist
    assert rules.blocked_words == ["spiders", "sharks"]
    assert rules.allowed_channels == ["Kids TV"]
    assert (rules.max_duration_minutes, rules.max_results) == (15, 6)


def test_flat_search_entries_keep_thumbnail_and_live_state():
    from app.backends.ytdlp import _entries_to_metadata
    metas = _entries_to_metadata([
        {"id": "aaaaaaaaaaa", "title": "A", "channel": "C", "duration": 61.0,
         "thumbnails": [{"url": "https://i.ytimg.com/vi/aaaaaaaaaaa/hq720.jpg?sqp=x&rs=y"}]},
        {"id": "bbbbbbbbbbb", "title": "B", "live_status": "is_upcoming"},
        {"id": "c1", "title": "Elsewhere",
         "thumbnails": [{"url": "https://cdn.example/small.jpg"},
                        {"url": "https://cdn.example/big.jpg"}]},
    ], parent_extractor="youtube")
    # Signed YouTube links answer with AVIF/WebP; the plain JPEG always works.
    assert metas[0].thumbnail_url == "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg"
    assert metas[1].thumbnail_url == "https://i.ytimg.com/vi/bbbbbbbbbbb/hqdefault.jpg"
    assert metas[2].thumbnail_url == "https://cdn.example/big.jpg"
    assert metas[0].duration == 61 and metas[0].extractor == "youtube"
    assert not metas[0].is_live and metas[1].is_live


def test_full_youtube_info_keeps_a_plain_jpeg_and_replaces_webp():
    from app.backends.ytdlp import _info_to_metadata
    jpg = _info_to_metadata({"id": "aaaaaaaaaaa", "extractor_key": "Youtube",
                             "thumbnail": "https://i.ytimg.com/vi/aaaaaaaaaaa/maxresdefault.jpg"})
    webp = _info_to_metadata({"id": "aaaaaaaaaaa", "extractor_key": "Youtube",
                              "thumbnail": "https://i.ytimg.com/vi_webp/aaaaaaaaaaa/maxresdefault.webp"})
    other = _info_to_metadata({"id": "x1", "extractor_key": "Vimeo",
                               "thumbnail": "https://i.vimeocdn.com/x.webp"})
    assert jpg.thumbnail_url == "https://i.ytimg.com/vi/aaaaaaaaaaa/maxresdefault.jpg"
    assert webp.thumbnail_url == "https://i.ytimg.com/vi/aaaaaaaaaaa/hqdefault.jpg"
    assert other.thumbnail_url == "https://i.vimeocdn.com/x.webp"


def test_more_videos_shows_the_next_page(app, client, backend):
    app.state.config.wishlist.max_results = 1
    app.state.config.wishlist.max_duration_minutes = 30
    try:
        _kid(app, client)
        first = client.get("/wishlist", params={"q": "songs"}).text
        assert "Counting to ten" in first and "Shapes song" not in first
        assert "page=2" in first and "page=0" not in first
        second = client.get("/wishlist", params={"q": "songs", "page": 2}).text
        assert "Shapes song" in second and "Counting to ten" not in second
        assert "page=1" in second and "page=3" not in second
        assert client.get("/wishlist/thumb/youtube_eeeeeeeeeee").status_code == 200
    finally:
        app.state.config.wishlist.max_results = 12


def test_the_same_search_asks_youtube_once(app, client, backend):
    _kid(app, client)
    client.get("/wishlist", params={"q": "songs"})
    client.get("/wishlist", params={"q": "Songs "})
    client.get("/wishlist", params={"q": "songs", "sort": "az"})
    assert backend.queries == ["songs"]
    client.get("/wishlist", params={"q": "songs", "sort": "newest"})
    assert backend.queries == ["songs", ("songs", True)]


def test_a_to_z_and_z_to_a(app, client, backend):
    _kid(app, client)
    az = client.get("/wishlist", params={"q": "songs", "sort": "az"}).text
    za = client.get("/wishlist", params={"q": "songs", "sort": "za"}).text
    assert az.index("Counting to ten") < az.index("Shapes song")
    assert za.index("Shapes song") < za.index("Counting to ten")
    assert 'aria-current="true">A to Z' in az


def test_tapping_too_fast_says_slow_down(app, client, backend):
    _kid(app, client)
    pages = [client.get("/wishlist", params={"q": "songs"}).text for _ in range(9)]
    assert "so many taps" not in pages[7]
    assert "so many taps" in pages[8] and "wishlist-error-slow" in pages[8]


def test_a_failed_search_is_not_nothing_found(app, client, backend):
    _kid(app, client)
    backend.fail = BackendErrorType.NETWORK
    page = client.get("/wishlist", params={"q": "songs"}).text
    assert "working right now" in page and "Nothing found" not in page
    backend.fail = None
    assert "Counting to ten" in client.get("/wishlist", params={"q": "songs"}).text


def test_nothing_found_when_the_rules_hide_everything(app, client, backend):
    app.state.config.wishlist.allowed_channels = ["Nobody"]
    try:
        _kid(app, client)
        page = client.get("/wishlist", params={"q": "songs"}).text
        assert "Nothing found" in page and "working right now" not in page
    finally:
        app.state.config.wishlist.allowed_channels = []


def test_a_bot_check_rests_searching_for_everyone(app, client, backend):
    _kid(app, client)
    backend.fail = BackendErrorType.BOT_BLOCKED
    assert "taking a nap" in client.get("/wishlist", params={"q": "songs"}).text
    backend.fail = None
    assert "taking a nap" in client.get("/wishlist", params={"q": "other"}).text
    assert backend.queries == ["songs"]
    assert GATE.status().resting_until > 0


def test_channels_search_applies_the_rules_and_opens_a_channel(app, client, backend):
    _kid(app, client)
    page = client.get("/wishlist", params={"q": "kids", "mode": "channels"}).text
    assert "Kids TV" in page and "Art Club" in page and "Scary Stories" not in page
    assert client.get(f"/wishlist/thumb/ytchannel_UC{'a' * 22}").status_code == 200
    channel = client.get(f"/wishlist/channel/UC{'a' * 22}", params={"q": "kids"}).text
    assert "Counting to ten" in channel and "Back to the channels" in channel
    assert "Shapes song" not in channel


def test_a_blocked_channel_cannot_be_opened_by_url(app, client, backend):
    _kid(app, client)
    page = client.get(f"/wishlist/channel/UC{'b' * 22}").text
    assert "be shown. Try another one" in page and "Spooky" not in page
    assert "be shown. Try another one" in client.get("/wishlist/channel/not-a-channel").text


def _videos(n):
    return [_meta(f"{i:011d}", f"Video {i}") for i in range(n)]


def test_gate_budgets():
    gate = SearchGate()
    call = lambda n: (_videos(1), None)  # noqa: E731
    for i in range(12):
        gate.fetch(("q", i), 1, kid=1, call=call)
    with pytest.raises(SearchRefused) as e:
        gate.fetch(("q", 99), 1, kid=1, call=call)
    assert e.value.kind == "slow"
    for i in range(18):
        gate.fetch(("q", 100 + i), 1, kid=2 + i, call=call)
    with pytest.raises(SearchRefused) as e:
        gate.fetch(("q", 999), 1, kid=50, call=call)
    assert e.value.kind == "busy"
    assert gate.fetch(("q", 0), 1, kid=50, call=call) == _videos(1)


def test_gate_asks_again_only_for_more_than_it_has():
    gate = SearchGate()
    asked = []

    def call(n):
        asked.append(n)
        return _videos(n), None
    gate.fetch(("q",), 24, kid=1, call=call)
    gate.fetch(("q",), 10, kid=1, call=call)
    gate.fetch(("q",), 48, kid=1, call=call)
    assert asked == [24, 48]
    short = SearchGate()
    short.fetch(("q",), 24, kid=1, call=lambda n: (_videos(2), None))
    assert short.fetch(("q",), 48, kid=1, call=lambda n: pytest.fail("asked again")) == _videos(2)


def test_the_cache_survives_a_restart_and_serves_the_parent(tmp_path):
    path = tmp_path / "cache.db"
    SearchGate(path).fetch(("video", "aaaaaaaaaaa"), 1, kid=1,
                           call=lambda n: ([RESULTS[0]], None))
    SearchGate(path).fetch(("channels", "kids"), 1, kid=1, call=lambda n: (CHANNELS[:1], None))
    again = SearchGate(path)
    assert again.video("aaaaaaaaaaa", lambda: pytest.fail("asked YouTube")) == RESULTS[0]
    assert again.fetch(("channels", "kids"), 1, kid=1,
                       call=lambda n: pytest.fail("asked YouTube")) == CHANNELS[:1]
    asked = []
    assert again.video("bbbbbbbbbbb", lambda: asked.append(1) or RESULTS[1]) == RESULTS[1]
    assert again.video("bbbbbbbbbbb", lambda: asked.append(1) or RESULTS[1]) == RESULTS[1]
    assert asked == [1]


def test_parent_add_preview_reuses_what_the_kid_looked_up(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    calls = []
    backend.fetch_video_info = lambda url: calls.append(url)
    page = authed_client.post("/parent/add",
                              data={"url": "https://youtu.be/aaaaaaaaaaa"}).text
    assert calls == [] and "Counting to ten" in page


def test_channel_listing_videos_get_youtube_ids():
    from app.backends.ytdlp import _entries_to_metadata
    metas = _entries_to_metadata([{"id": "aaaaaaaaaaa", "ie_key": "Youtube", "title": "A"}],
                                 "Kids TV", parent_extractor="youtubetab")
    assert metas[0].extractor == "youtube" and metas[0].channel == "Kids TV"


def test_the_dashboard_says_when_search_is_paused_or_failing(app, client, authed_client, backend):
    _kid(app, client)
    assert "video search" not in authed_client.get("/parent/").text
    backend.fail = BackendErrorType.NETWORK
    client.get("/wishlist", params={"q": "one"})
    client.get("/wishlist", params={"q": "two"})
    assert "video search failed 2 times" in authed_client.get("/parent/").text
    backend.fail = BackendErrorType.BOT_BLOCKED
    client.get("/wishlist", params={"q": "three"})
    assert "video search is paused until" in authed_client.get("/parent/").text


def test_parent_channel_and_playlist_previews_are_cached(app, authed_client, backend):
    calls = []

    def channel(url, max_results=50):
        calls.append(url)
        return "Kids TV", list(RESULTS[:2])
    backend.fetch_channel_videos = channel
    backend.fetch_playlist = lambda url, *a, **k: calls.append(url) or ("Songs", list(RESULTS[:1]))
    for _ in range(2):
        page = authed_client.post("/parent/add", data={"url": "https://www.youtube.com/@kidstv"}).text
        assert "Kids TV" in page and "Scary monster night" in page
        page = authed_client.post("/parent/add", data={
            "url": "https://www.youtube.com/playlist?list=PL" + "a" * 32}).text
        assert "Songs" in page and "Counting to ten" in page
    assert len(calls) == 2


def test_an_unreadable_cache_row_is_a_miss(tmp_path):
    import sqlite3
    path = tmp_path / "cache.db"
    SearchGate(path)
    db = sqlite3.connect(path)
    db.execute("INSERT INTO answers VALUES (?, 9e12, 1, '[]')", ('["video", "aaaaaaaaaaa"]',))
    db.commit()
    assert SearchGate(path).video("aaaaaaaaaaa", lambda: RESULTS[0]) == RESULTS[0]


def test_a_parent_lookup_never_waits_forever(monkeypatch):
    import app.services.search_gate as sg
    monkeypatch.setattr(sg, "LOCK_WAIT_SECONDS", 0.1)
    gate = SearchGate()
    gate._youtube.acquire()
    assert gate.video("aaaaaaaaaaa", lambda: RESULTS[0]) == RESULTS[0]


def test_the_kid_never_sees_the_youtube_description(app, client, authed_client, backend):
    from dataclasses import replace
    backend.fetch_video_info = lambda url: replace(RESULTS[0], description="Subscribe for $3.99 at shop.example")
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    item = WishlistRepository(_conn(app)).list_by_status("pending")[0]
    assert "Subscribe" not in client.get(f"/wishlist/asked/{item.id}").text
    client.post(f"/wishlist/asked/{item.id}", data={"title": "", "description": ""})
    review = authed_client.get(f"/parent/requests/{item.id}/review").text
    assert ">Subscribe for $3.99 at shop.example</textarea>" in review
    assert "This text comes from YouTube. Kids see it, so read it first" in review
    authed_client.post(f"/parent/requests/{item.id}/reject")
    mine = client.get("/wishlist").text.split("What I asked for")[1]
    assert "Counting to ten" in mine and "Not this time" in mine


def test_asking_for_a_shown_video_records_it_from_the_search(app, client, backend):
    from dataclasses import replace
    _kid(app, client)
    client.get("/wishlist", params={"q": "songs"})
    calls = []

    def full(url):
        calls.append(url)
        return replace(RESULTS[0], description="All about numbers", upload_date="20260101")
    backend.fetch_video_info = full
    assert client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"}).status_code == 303
    item = WishlistRepository(_conn(app)).list_by_status("pending")[0]
    assert len(calls) == 1
    assert (item.original_description, item.upload_date) == ("All about numbers", "20260101")


def test_full_details_that_break_the_rules_turn_the_request_down(app, client, authed_client, backend):
    from dataclasses import replace
    authed_client.post("/parent/settings/wishlist", data={"blocked_words": "scary", "allowed_channels": "",
                                                           "max_duration_minutes": "30", "max_results": "12"})
    _kid(app, client)
    client.get("/wishlist", params={"q": "songs"})
    backend.fetch_video_info = lambda url: replace(RESULTS[0], description="A scary story")
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    repo = WishlistRepository(_conn(app))
    assert repo.list_by_status("pending") == []
    assert repo.list_by_status("rejected")[0].video_id == "youtube_aaaaaaaaaaa"


def test_review_fetches_details_the_lookup_after_ask_missed(app, client, authed_client, backend):
    from dataclasses import replace
    _kid(app, client)
    client.get("/wishlist", params={"q": "songs"})
    backend.fetch_video_info = lambda url: None
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    item = WishlistRepository(_conn(app)).list_by_status("pending")[0]
    assert not item.has_details
    GATE.reset()
    backend.fetch_video_info = lambda url: replace(RESULTS[0], description="Numbers", upload_date="20260101")
    review = authed_client.get(f"/parent/requests/{item.id}/review").text
    assert ">Numbers</textarea>" in review
    assert WishlistRepository(_conn(app)).get(item.id).has_details


def test_empty_screens_point_to_find_new_only_when_it_is_on(app, client, backend):
    _kid(app, client, "wren", search_mode="curated")
    assert 'href="/wishlist" class="wishlist-pill is-on empty-find-new"' in client.get("/").text
    assert 'href="/wishlist?q=dinosaurs"' in client.get("/search", params={"q": "dinosaurs"}).text
    _kid(app, client, "otto", wishlist=False)
    assert "empty-find-new" not in client.get("/").text


def test_playlist_listings_leave_out_videos_that_are_gone():
    from app.backends.ytdlp import _entries_to_metadata
    metas = _entries_to_metadata([
        {"id": "aaaaaaaaaaa", "title": "Five Little Ducks"},
        {"id": "bbbbbbbbbbb", "title": None, "duration": None},
        {"id": "ccccccccccc", "title": "[Private video]"},
        {"id": "ddddddddddd"},
    ], parent_extractor="youtube")
    assert [m.title for m in metas] == ["Five Little Ducks"]
    elsewhere = _entries_to_metadata([{"id": "v1"}], parent_extractor="vimeo")
    assert [m.title for m in elsewhere] == ["Untitled video"]


def test_review_leaves_out_a_video_the_library_already_has(app, authed_client, backend):
    from app.models import Video
    from app.repositories import ChannelRepository, VideoRepository
    conn = _conn(app)
    songs = ChannelRepository(conn).create("Songs")
    VideoRepository(conn).insert(Video(
        video_id="youtube_aaaaaaaaaaa", extractor="youtube", title="My counting song",
        original_title="Counting to ten", channel_id=songs, status="active", download_status="ready"))
    conn.commit()
    page = authed_client.post("/parent/add", data={"url": "https://youtu.be/aaaaaaaaaaa"}).text
    assert "Already in your library, so left out" in page and "My counting song (Songs)" in page
    assert "nothing new to add" in page and "watched_aaaaaaaaaaa" not in page


def test_search_says_it_looks_in_the_kids_own_videos(app, client, backend):
    _kid(app, client, "wren", search_mode="curated")
    page = client.get("/search").text
    assert "Search my videos" in page and "Find new videos" not in page


def test_a_video_youtube_wont_serve_is_marked_unavailable(app, client, authed_client, backend):
    from app.backends.base import BackendErrorType
    from app.services.wishlist import WishlistService
    from app.repositories import VideoRepository
    pid = _kid(app, client)
    repo = WishlistRepository(_conn(app))
    request_id = repo.add(pid, "youtube_zzzzzzzzzzz", _meta("zzzzzzzzzzz", "Gone video"),
                          "https://www.youtube.com/watch?v=zzzzzzzzzzz")

    def gone(url):
        backend.last_error = BackendError(BackendErrorType.VIDEO_UNAVAILABLE, "not available", "")
        return None
    backend.fetch_video_info = gone
    WishlistService(backend, repo, VideoRepository(_conn(app)),
                    FakeThumbnails(app.state.config.data_dir), app.state.config).fill_details(request_id)
    assert repo.get(request_id).status == "unavailable"
    resp = authed_client.get(f"/parent/requests/{request_id}/review")
    assert resp.headers["location"] == "/parent/requests?notice=unavailable"
    assert "YouTube won't serve it" in authed_client.get("/parent/requests").text
    assert "Can&#39;t get this one" in client.get("/wishlist").text


def test_kid_history_hides_a_turned_down_title_with_a_blocked_word(app, client, authed_client, backend):
    pid = _kid(app, client)
    repo = WishlistRepository(_conn(app))
    request_id = repo.add(pid, "youtube_yyyyyyyyyyy", _meta("yyyyyyyyyyy", "Pirate night"),
                          "https://www.youtube.com/watch?v=yyyyyyyyyyy")
    repo.decide(request_id, "rejected")
    assert "Pirate night" in client.get("/wishlist").text
    app.state.config.wishlist.blocked_words = ["pirate"]
    page = client.get("/wishlist").text
    assert "Pirate night" not in page and "A video" in page


def test_a_channel_full_of_filtered_videos_still_offers_more(app, client, backend):
    _kid(app, client)
    app.state.config.wishlist.max_duration_minutes = 30
    long_ones = [_meta(f"l{i:010d}", f"Compilation {i}", duration=7200) for i in range(44)]
    short_ones = [_meta(f"s{i:010d}", f"Short {i}") for i in range(4)]

    def listing(url, max_results=50):
        backend.queries.append(("channel", max_results))
        return "Kids TV", (short_ones + long_ones)[:max_results]
    backend.fetch_channel_videos = listing
    page = client.get("/wishlist/channel/UC" + "a" * 22).text
    assert backend.queries[-1] == ("channel", 48)
    assert page.count('class="wishlist-ask-btn"') == 4
    assert "&amp;page=2" in page


def test_requests_list_shows_the_kids_note_and_channel(app, client, authed_client, backend):
    from app.repositories.channel_repo import ChannelRepository
    dino = ChannelRepository(_conn(app)).create("Dinosaurs")
    _kid(app, client)
    client.get("/wishlist", params={"q": "counting"})
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    request_id = WishlistRepository(_conn(app)).list_by_status("pending")[0].id
    client.post(f"/wishlist/asked/{request_id}", data={
        "title": "Counting", "description": "To dance with my brother", "channel_id": str(dino)})
    page = authed_client.get("/parent/requests").text
    assert "For: <strong>Dinosaurs</strong>" in page
    assert "To dance with my brother" in page


def test_a_channel_preview_says_when_only_the_newest_50_are_listed(app, authed_client, backend):
    backend.fetch_channel_videos = lambda url, max_results=50: (
        "Big Channel", [_meta(f"b{i:010d}", f"Episode {i}") for i in range(50)])
    page = authed_client.post("/parent/add", data={"url": "https://www.youtube.com/@big"}).text
    assert "Only the newest 50 are listed; add older ones by their own link" in page


def test_length_is_unlimited_until_the_parent_sets_a_limit(app, client, backend):
    from app.config import WishlistConfig
    assert WishlistConfig().max_duration_minutes == 0
    _kid(app, client)
    app.state.config.wishlist.max_duration_minutes = 0
    assert "Two hour lullabies" in client.get("/wishlist", params={"q": "songs"}).text
