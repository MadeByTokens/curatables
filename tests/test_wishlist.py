"""Kid wishlist: server-side YouTube search, filtered, approved by a parent."""
import pytest

from app.backends.base import VideoMetadata
from app.dependencies import get_backend, get_db, get_thumbnail_service
from app.repositories import ProfileRepository, VideoRepository
from app.repositories.wishlist_repo import WishlistRepository
from app.services.profiles import ProfileService
from app.services.thumbnails import ThumbnailService
from app.services.wishlist import SEARCH_LIMITER

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


class FakeBackend:
    last_error = None

    def __init__(self):
        self.queries = []

    def search(self, query, max_results=12):
        self.queries.append(query)
        return list(RESULTS)

    def fetch_video_info(self, url):
        raw = url.rsplit("=", 1)[-1]
        return next((m for m in RESULTS if m.video_id == raw), None)


class FakeThumbnails(ThumbnailService):
    def download(self, video_id, thumbnail_url=None):
        path = self.get_path(video_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(JPEG)
        return path


@pytest.fixture
def backend(app):
    fake = FakeBackend()
    app.dependency_overrides[get_backend] = lambda: fake
    app.dependency_overrides[get_thumbnail_service] = lambda: FakeThumbnails(app.state.config.data_dir)
    SEARCH_LIMITER.reset()
    yield fake
    app.dependency_overrides.pop(get_backend, None)
    app.dependency_overrides.pop(get_thumbnail_service, None)


def _conn(app):
    return next(app.dependency_overrides[get_db]())


def _kid(app, client, name="wren", wishlist=True):
    svc = ProfileService(ProfileRepository(_conn(app)))
    pid = svc.create(name=name, display_name=name.title(), allow_wishlist=wishlist)
    assert client.post("/profiles/select", data={"profile_id": pid}).status_code == 302
    return pid


def test_wishlist_is_off_unless_the_parent_switches_it_on(app, client, backend):
    _kid(app, client, wishlist=False)
    assert client.get("/wishlist").status_code == 302
    assert client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"}).status_code in (302, 403)
    assert backend.queries == []


def test_search_applies_the_parents_rules(app, client, backend):
    _kid(app, client)
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
    page = client.get("/wishlist", params={"q": "songs"}).text
    assert "Counting to ten</span>" not in page.split("What I asked for")[0]
    assert "Not this time" in page
    again = client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"}).text
    assert "not this one" in again


def test_adding_the_video_approves_the_request(app, client, authed_client, backend):
    _kid(app, client)
    client.post("/wishlist/ask", data={"raw_id": "aaaaaaaaaaa"})
    from unittest.mock import patch
    with patch("app.services.content.ContentService.add_video", return_value=object()):
        resp = authed_client.post("/parent/add/confirm", data={
            "url_type": "video", "source_url": "https://www.youtube.com/watch?v=aaaaaaaaaaa",
            "source_title": "Counting to ten", "video_ids": "aaaaaaaaaaa",
            "extractor_aaaaaaaaaaa": "youtube", "watched_aaaaaaaaaaa": "1",
        })
    assert resp.status_code == 200
    repo = WishlistRepository(_conn(app))
    assert repo.list_by_status("pending") == []
    assert repo.list_by_status("approved")[0].video_id == "youtube_aaaaaaaaaaa"


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
