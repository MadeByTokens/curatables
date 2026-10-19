"""Trusted YouTube channels: by ID, for the family and per kid."""
import importlib.util
import sqlite3

import pytest

from app.backends.base import ChannelHit
from app.db.migrator import migrations_dir
from app.dependencies import get_backend, get_db
from app.repositories import ProfileRepository
from app.repositories.trusted_channel_repo import TrustedChannelRepository
from app.repositories.wishlist_repo import WishlistRepository
from app.services.profiles import ProfileService
from app.services.trusted_channels import TrustedChannelError, TrustedChannelService, channel_link

KIDS_TV = "UC" + "a" * 22
ART = "UC" + "c" * 22


class FakeBackend:
    last_error = None

    def __init__(self):
        self.asked = []
        self.answer = True

    def find_channel(self, url):
        self.asked.append(url)
        return ChannelHit(KIDS_TV, "  Kids   TV ") if self.answer else None

    def search_channels(self, query, max_results=12):
        return [ChannelHit(KIDS_TV, "Kids TV", "", 1200), ChannelHit("not-an-id", "Broken"),
                ChannelHit(ART, "Art Club")]


@pytest.fixture
def backend(app):
    fake = FakeBackend()
    app.dependency_overrides[get_backend] = lambda: fake
    yield fake
    app.dependency_overrides.pop(get_backend, None)


def _conn(app):
    return next(app.dependency_overrides[get_db]())


def _repo(app):
    return TrustedChannelRepository(_conn(app))


def _kid(app, name="ivy"):
    return ProfileService(ProfileRepository(_conn(app))).create(name=name, display_name=name.title())


@pytest.mark.parametrize("text, url", [
    (KIDS_TV, f"https://www.youtube.com/channel/{KIDS_TV}"),
    (f" https://www.youtube.com/channel/{KIDS_TV}/featured ", f"https://www.youtube.com/channel/{KIDS_TV}"),
    ("@kidstv", "https://www.youtube.com/@kidstv"),
    ("youtube.com/@kidstv/videos", "https://www.youtube.com/@kidstv"),
    ("https://m.youtube.com/c/KidsTV", "https://www.youtube.com/c/KidsTV"),
    ("https://www.youtube.com/watch?v=aaaaaaaaaaa&t=3", "https://www.youtube.com/watch?v=aaaaaaaaaaa"),
    ("https://youtu.be/aaaaaaaaaaa?si=x", "https://www.youtube.com/watch?v=aaaaaaaaaaa"),
    ("https://www.youtube.com/shorts/aaaaaaaaaaa", "https://www.youtube.com/watch?v=aaaaaaaaaaa"),
    ("Numberblocks", None),
    ("https://evil.example/@kidstv", None),
    ("https://www.youtube.com.evil.example/@kidstv", None),
    ("http://192.168.1.1/channel/" + KIDS_TV, None),
    ("ftp://www.youtube.com/@kidstv", None),
    ("https://www.youtube.com/watch?v=short", None),
    ("https://www.youtube.com/", None),
    ("", None),
    ("http://[abc", None),
    ("x[", None),
])
def test_channel_link(text, url):
    assert channel_link(text) == url


def test_lookup_asks_youtube_and_tidies_the_name():
    backend = FakeBackend()
    svc = TrustedChannelService(TrustedChannelRepository(sqlite3.connect(":memory:")), backend)
    found = svc.lookup("@kidstv")
    assert (found.channel_id, found.name) == (KIDS_TV, "Kids TV")
    backend.answer = False
    with pytest.raises(TrustedChannelError):
        svc.lookup("@kidstv")
    # An ID needs no answer: the rules only use the ID.
    assert svc.lookup(ART).channel_id == ART
    with pytest.raises(TrustedChannelError):
        svc.lookup("not a channel")


def test_family_and_kid_lists(app):
    repo = _repo(app)
    ivy, otto = _kid(app, "ivy"), _kid(app, "otto")
    repo.add_family(KIDS_TV, "Kids TV")
    repo.add_family(ART, "Art Club")
    repo.add_for(ivy, "UC" + "e" * 22, "Ivy's pick")
    repo.set_switched_off(ivy, {ART})
    assert repo.ids_for(ivy) == {KIDS_TV, "UC" + "e" * 22}
    assert repo.ids_for(otto) == {KIDS_TV, ART}
    assert [c.name for c in repo.extra_for(ivy)] == ["Ivy's pick"]
    # Ticking it again brings the family channel back for the kid.
    repo.set_switched_off(ivy, set())
    assert ART in repo.ids_for(ivy)
    # Adding a channel for every kid, or taking it off for every kid,
    # replaces what one kid had for it.
    repo.set_switched_off(ivy, {ART})
    repo.add_family(ART, "Art Club")
    assert ART in repo.ids_for(ivy)
    repo.add_for(otto, ART, "Art Club")
    assert repo.extra_for(otto) == []
    repo.set_switched_off(ivy, {ART})
    repo.remove_family(ART)
    assert ART not in repo.ids_for(otto) and repo.extra_for(otto) == []
    assert repo.switched_off_for(ivy) == set()
    # Deleting a kid deletes their list.
    ProfileRepository(_conn(app)).delete(otto)
    assert _conn(app).execute("SELECT COUNT(*) FROM profile_youtube_channels "
                              "WHERE profile_id = ?", (otto,)).fetchone()[0] == 0


def test_parent_adds_and_removes_family_channels(app, authed_client, backend):
    resp = authed_client.post("/parent/trusted-channels/add", data={"channel": "@kidstv"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/parent/trusted-channels?notice=trusted"
    assert [(c.channel_id, c.name) for c in _repo(app).family()] == [(KIDS_TV, "Kids TV")]
    page = authed_client.get("/parent/trusted-channels?notice=trusted").text
    assert "Channel added." in page and KIDS_TV in page
    resp = authed_client.post("/parent/trusted-channels/remove", data={"channel_id": KIDS_TV})
    assert resp.headers["location"] == "/parent/trusted-channels?notice=untrusted"
    assert _repo(app).family() == []


def test_a_bad_link_says_why(app, authed_client, backend):
    resp = authed_client.post("/parent/trusted-channels/add", data={"channel": "Numberblocks"})
    assert resp.status_code == 400 and "isn&#39;t a YouTube channel" in resp.text
    backend.answer = False
    resp = authed_client.post("/parent/trusted-channels/add", data={"channel": "@kidstv"})
    assert resp.status_code == 400 and "didn&#39;t say which channel" in resp.text
    resp = authed_client.post("/parent/trusted-channels/add", data={"channel_id": "nope", "name": "x"})
    assert resp.status_code == 400
    assert _repo(app).family() == []


def test_adding_for_one_kid_never_falls_back_to_everyone(app, authed_client, backend):
    ivy = _kid(app)
    authed_client.post("/parent/trusted-channels/add", data={
        "channel_id": ART, "name": "Art Club", "profile_id": str(ivy)})
    assert _repo(app).family() == [] and _repo(app).ids_for(ivy) == {ART}
    resp = authed_client.post("/parent/trusted-channels/add", data={
        "channel_id": KIDS_TV, "name": "Kids TV", "profile_id": "999"})
    assert resp.status_code == 303 and _repo(app).family() == []
    authed_client.post("/parent/trusted-channels/remove", data={"channel_id": ART, "profile_id": "999"})
    assert _repo(app).ids_for(ivy) == {ART}
    authed_client.post("/parent/trusted-channels/remove", data={"channel_id": ART, "profile_id": str(ivy)})
    assert _repo(app).ids_for(ivy) == set()


def test_parent_finds_a_channel_by_name(app, authed_client, backend):
    _kid(app)
    page = authed_client.get("/parent/trusted-channels", params={"find": "kids"}).text
    assert f'name="channel_id" value="{KIDS_TV}"' in page
    assert f'name="channel_id" value="{ART}"' in page
    assert "Broken" not in page and "1,200 subscribers" in page
    assert "Only Ivy" in page


def test_back_goes_only_to_parent_pages(app, authed_client, backend):
    for back, where in [("/parent/requests", "/parent/requests?notice=trusted"),
                        ("https://evil.example/", "/parent/trusted-channels?notice=trusted"),
                        ("//evil.example/parent/", "/parent/trusted-channels?notice=trusted"),
                        ("/parent/trusted-channels?find=a#find",
                         "/parent/trusted-channels?find=a&notice=trusted#find")]:
        resp = authed_client.post("/parent/trusted-channels/add", data={
            "channel_id": KIDS_TV, "name": "Kids TV", "back": back})
        assert resp.headers["location"] == where


def test_pages_need_the_parent(client):
    assert client.get("/parent/trusted-channels").status_code in (302, 303, 401, 403)
    resp = client.post("/parent/trusted-channels/add", data={"channel_id": KIDS_TV, "name": "x"})
    assert resp.status_code in (302, 303, 401, 403)


def test_kid_form_sets_their_channels(app, authed_client, backend):
    repo = _repo(app)
    repo.add_family(KIDS_TV, "Kids TV")
    repo.add_family(ART, "Art Club")
    ivy = _kid(app)
    repo.add_for(ivy, "UC" + "e" * 22, "Ivy's pick")
    form = authed_client.get(f"/parent/profiles/{ivy}/edit").text
    assert f'name="trusted_family" value="{ART}" checked' in form
    assert 'name="trusted_extra" value="UC' + "e" * 22 + '" checked' in form
    # A family channel added after the form was opened isn't switched off.
    repo.add_family("UC" + "f" * 22, "Late Show")
    resp = authed_client.post(f"/parent/profiles/{ivy}/edit", data={
        "display_name": "Ivy", "youtube_section": "1", "trusted_only": "on",
        "trusted_family_shown": [KIDS_TV, ART],
        "trusted_family": [KIDS_TV], "add_channel": "@kidstv"})
    assert resp.status_code == 302
    assert repo.switched_off_for(ivy) == {ART}
    assert repo.ids_for(ivy) == {KIDS_TV, "UC" + "f" * 22}
    assert ProfileRepository(_conn(app)).get(ivy).trusted_only == "on"
    form = authed_client.get(f"/parent/profiles/{ivy}/edit").text
    assert f'name="trusted_family" value="{ART}">' in form
    # Switched off stays so when a later form doesn't show that channel.
    authed_client.post(f"/parent/profiles/{ivy}/edit", data={
        "display_name": "Ivy", "youtube_section": "1", "trusted_family_shown": [KIDS_TV],
        "trusted_family": [KIDS_TV]})
    assert repo.switched_off_for(ivy) == {ART}
    # A form without the section (an older page) changes none of it.
    authed_client.post(f"/parent/profiles/{ivy}/edit", data={"display_name": "Ivy"})
    assert repo.switched_off_for(ivy) == {ART}


def test_kid_form_keeps_the_rest_when_the_channel_isnt_found(app, authed_client, backend):
    ivy = _kid(app)
    backend.answer = False
    resp = authed_client.post(f"/parent/profiles/{ivy}/edit", data={
        "display_name": "Ivy Rose", "youtube_section": "1", "add_channel": "@nobody"})
    assert resp.status_code == 400
    assert "Everything else was saved" in resp.text and 'value="@nobody"' in resp.text
    assert ProfileRepository(_conn(app)).get(ivy).display_name == "Ivy Rose"


def test_new_kid_gets_a_channel_of_their_own(app, authed_client, backend):
    resp = authed_client.post("/parent/profiles/create", data={
        "display_name": "Otto", "youtube_section": "1", "add_channel": KIDS_TV})
    assert resp.status_code == 302
    otto = next(p for p in ProfileRepository(_conn(app)).list() if p.display_name == "Otto")
    assert _repo(app).ids_for(otto.id) == {KIDS_TV}


def test_requests_page_offers_to_trust_the_channel(app, authed_client, backend):
    from app.backends.base import VideoMetadata
    ivy = _kid(app)
    meta = VideoMetadata("aaaaaaaaaaa", "Counting", "Kids TV", 60, 0, "", "", "", "youtube",
                         channel_id=KIDS_TV)
    WishlistRepository(_conn(app)).add(ivy, "youtube_aaaaaaaaaaa", meta,
                                       "https://www.youtube.com/watch?v=aaaaaaaaaaa")
    page = authed_client.get("/parent/requests").text
    assert f'name="channel_id" value="{KIDS_TV}"' in page and "Only Ivy" in page
    resp = authed_client.post("/parent/trusted-channels/add", data={
        "channel_id": KIDS_TV, "name": "Kids TV", "profile_id": str(ivy), "back": "/parent/requests"})
    assert resp.headers["location"] == "/parent/requests?notice=trusted"
    page = authed_client.get(resp.headers["location"]).text
    assert "Channel trusted." in page and "is a <a" in page
    assert f'name="channel_id" value="{KIDS_TV}"' not in page


def test_settings_links_to_the_trusted_channels(authed_client):
    page = authed_client.get("/parent/settings").text
    assert 'href="/parent/trusted-channels"' in page and "allowed_channels" not in page


def _load(name):
    path = next(p for p in migrations_dir().iterdir() if p.name.startswith(name))
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_migration_keeps_kids_whose_requests_went_into_a_channel(tmp_path):
    from app.db.migrator import apply_pending
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    apply_pending(conn, migrations_dir())
    conn.execute("ALTER TABLE profiles DROP COLUMN auto_add_mode")
    conn.execute("ALTER TABLE profiles DROP COLUMN trusted_only")
    conn.execute("INSERT INTO channels (name) VALUES ('From YouTube')")
    conn.execute("INSERT INTO profiles (name, auto_add_channel_id) VALUES ('a', 1), ('b', NULL)")
    _load("0018_").up(conn)
    _load("0018_").up(conn)
    rows = conn.execute("SELECT name, auto_add_mode, trusted_only FROM profiles ORDER BY name").fetchall()
    assert [tuple(r) for r in rows] == [("a", "on", "family"), ("b", "family", "family")]


def test_cached_videos_without_a_channel_id_are_asked_again(tmp_path):
    import json
    import time
    from app.services.search_gate import _Cache
    cache = _Cache(tmp_path / "answers.db")
    old = {"video_id": "aaaaaaaaaaa", "title": "A", "channel": "Kids TV", "duration": 1,
           "view_count": 0, "upload_date": "", "description": "", "thumbnail_url": "",
           "extractor": "youtube", "original_url": "", "is_live": False}
    cache._db.execute("INSERT INTO answers VALUES (?, ?, ?, ?)",
                      (json.dumps(["videos", "x"]), time.time() + 60, 1,
                       json.dumps({"title": "", "items": [["video", old]]})))
    assert cache.get(("videos", "x")) is None
    cache._db.execute("UPDATE answers SET items = ?", (json.dumps(
        {"title": "", "items": [["video", {**old, "channel_id": KIDS_TV}]]}),))
    assert cache.get(("videos", "x"))[2][0].channel_id == KIDS_TV
