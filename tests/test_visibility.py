"""Who can see which videos: the single visibility policy in ContentService."""

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository


def _conn(app):
    return next(app.dependency_overrides[get_db]())


def _video(conn, video_id, channel_id, title="Clip"):
    VideoRepository(conn).insert(Video(
        video_id=video_id, title=title, original_title=title,
        channel_id=channel_id, status="active", download_status="ready",
    ))


def _kid(conn, name, allowed=None, restricted=None, search_mode="curated"):
    return ProfileRepository(conn).create(Profile(
        name=name, display_name=name, pin="", search_mode=search_mode,
        allowed_channel_ids=allowed or [],
        channels_restricted=bool(allowed) if restricted is None else restricted,
    ))


def _as_kid(app, pid):
    client = TestClient(app, follow_redirects=False)
    client.post("/profiles/select", data={"profile_id": pid})
    return client


def test_deleting_only_whitelisted_channel_keeps_kid_restricted(app):
    conn = _conn(app)
    channels = ChannelRepository(conn)
    allowed = channels.create("Allowed")
    other = channels.create("Other")
    _video(conn, "yt_allowed", allowed, "Allowed clip")
    _video(conn, "yt_other", other, "Other clip")
    pid = _kid(conn, "ana", allowed=[allowed])

    channels.delete(allowed)

    kid = _as_kid(app, pid)
    home = kid.get("/")
    assert home.status_code == 200
    assert "Other clip" not in home.text
    assert kid.get("/media/video/yt_other").status_code == 404


def test_search_with_no_visible_channels_returns_nothing(app):
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Hidden")
    _video(conn, "yt_hidden", ch, "Dinosaur song")
    _video(conn, "yt_uncat", None, "Dinosaur uncategorized")
    pid = _kid(conn, "ben", restricted=True)

    resp = _as_kid(app, pid).get("/search?q=Dinosaur")
    assert resp.status_code == 200
    assert "Dinosaur song" not in resp.text
    assert "Dinosaur uncategorized" not in resp.text


def test_anonymous_cannot_stream_or_list_subtitles(app, client):
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Any")
    _video(conn, "yt_any", ch)
    _kid(conn, "cai")
    assert client.get("/media/video/yt_any").status_code == 404
    assert client.get("/media/subs/yt_any").status_code == 404
    assert client.get(f"/media/channel/{ch}/icon").status_code == 404


def test_a_misspelled_search_offers_close_videos_the_kid_may_see(app):
    """Nothing spelled that way: videos with a word close to it, under
    "Did you mean", and only from the kid's own channels. A word that is
    spelled right shows its videos alone."""
    conn = _conn(app)
    channels = ChannelRepository(conn)
    allowed = channels.create("Allowed")
    other = channels.create("Other")
    _video(conn, "yt_dino", allowed, "Dinosaur song")
    _video(conn, "yt_dino2", other, "Dinosaurs for grown-ups")
    _video(conn, "yt_park", allowed, "Park day")
    _video(conn, "yt_dark", allowed, "Dark sky")
    kid = _as_kid(app, _kid(conn, "dee", allowed=[allowed]))

    page = kid.get("/search?q=dinasor").text
    assert "Did you mean one of these?" in page
    assert "Dinosaur song" in page and "Dinosaurs for grown-ups" not in page
    page = kid.get("/search?q=park").text
    assert "Park day" in page and "Dark sky" not in page and "Did you mean" not in page
    page = kid.get("/search?q=xylophone").text
    assert "Did you mean" not in page and "Nothing found" in page


def test_kid_nav_hides_non_whitelisted_channels(app):
    conn = _conn(app)
    channels = ChannelRepository(conn)
    allowed = channels.create("Trains")
    also = channels.create("Boats")
    channels.create("Secret Grownup Channel")
    _video(conn, "yt_train", allowed, "Train clip")
    _video(conn, "yt_boat", also, "Boat clip")
    pid = _kid(conn, "dan", allowed=[allowed, also])

    kid = _as_kid(app, pid)
    page = kid.get("/channels")
    assert "Trains" in page.text and "Boats" in page.text
    assert "Secret Grownup Channel" not in page.text


def test_kid_cannot_open_hidden_channel_page(app):
    conn = _conn(app)
    channels = ChannelRepository(conn)
    allowed = channels.create("Trains")
    hidden = channels.create("Hidden")
    pid = _kid(conn, "eve", allowed=[allowed])

    resp = _as_kid(app, pid).get(f"/channel/{hidden}")
    assert resp.status_code == 302
    assert resp.headers["location"] == "/"


def test_parent_can_view_uncategorized(app):
    from app.models import ViewerContext
    conn = _conn(app)
    _video(conn, "yt_uncat2", None)
    video = VideoRepository(conn).get("yt_uncat2")
    assert video is not None
    from app.services.content import ContentService
    content = ContentService(VideoRepository(conn), None,
                             ChannelRepository(conn), None, None, None,
                             app.state.config)
    assert content.can_view(video, ViewerContext(viewer_type="parent"))
    assert not content.can_view(video, ViewerContext(viewer_type="anonymous"))


def test_picking_kid_profile_logs_parent_out(app, authed_client):
    conn = _conn(app)
    pid = _kid(conn, "fay")
    _kid(conn, "gus")
    authed_client.post("/profiles/select", data={"profile_id": pid})
    authed_client.get("/profiles/switch")
    resp = authed_client.get("/parent/")
    assert resp.status_code == 302
    assert "/parent/login" in resp.headers["location"]


def test_kid_channel_color_rejects_css_injection(app):
    conn = _conn(app)
    pid = _kid(conn, "hal")
    ch = ChannelRepository(conn).create("Hal's", owner_profile_id=pid)
    kid = _as_kid(app, pid)
    kid.post(f"/channel/{ch}/edit", data={
        "name": "Hal's", "color": "red;background:url(//evil.example)"})
    assert ChannelRepository(conn).get(ch).color == "#2a9d8f"
    kid.post(f"/channel/{ch}/edit", data={"name": "Hal's", "color": "#123abc"})
    assert ChannelRepository(conn).get(ch).color == "#123abc"


def test_parent_delete_confirm_uses_data_attribute(app, authed_client):
    conn = _conn(app)
    ChannelRepository(conn).create("x');alert(1);('")
    page = authed_client.get("/parent/channels/").text
    assert "confirm('Delete channel" not in page
    assert 'data-confirm="Delete channel x&#39;);alert(1);(&#39;?' in page


def test_unknown_page_shows_friendly_error(app):
    resp = _as_kid(app, _kid(_conn(app), "jay")).get("/watch/does-not-exist/nope")
    assert resp.status_code == 404
    assert "Home" in resp.text
    assert '"detail"' not in resp.text


def test_bad_query_param_shows_friendly_error(app):
    resp = _as_kid(app, _kid(_conn(app), "kai")).get("/channel/abc")
    assert resp.status_code == 400
    assert "Home" in resp.text


def test_api_errors_stay_json(app):
    resp = _as_kid(app, _kid(_conn(app), "lou")).post("/api/react", data={})
    assert resp.status_code == 422
    assert resp.json()["detail"]


def test_comment_on_hidden_video_is_refused(app):
    conn = _conn(app)
    channels = ChannelRepository(conn)
    hidden = channels.create("Hidden")
    allowed = channels.create("Allowed")
    _video(conn, "yt_secret", hidden)
    pid = _kid(conn, "ivy", allowed=[allowed])
    resp = _as_kid(app, pid).post("/watch/yt_secret/comment", data={"body": "hi"})
    assert resp.status_code == 404
    assert conn.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0


def test_kid_search_shows_and_finds_the_kids_own_title(app):
    from app.repositories.override_repo import ProfileVideoOverrideRepository
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Science")
    _video(conn, "yt_volcano", ch, "Baking soda volcano")
    pid = _kid(conn, "maya")
    ProfileVideoOverrideRepository(conn).upsert(pid, "yt_volcano", title="Vulcão de bicarbonato")

    kid = _as_kid(app, pid)
    found = kid.get("/search?q=vulcao").text
    assert "Vulcão de bicarbonato" in found
    assert "Vulcão de bicarbonato" in kid.get("/search?q=volcano").text
    assert "Baking soda volcano" not in kid.get("/search?q=volcano").text


def test_empty_channels_are_not_offered_to_kids(app):
    conn = _conn(app)
    channels = ChannelRepository(conn)
    animals = channels.create("Animals")
    science = channels.create("Science")
    bedtime = channels.create("Bedtime")
    _video(conn, "yt_zoo", animals, "Me at the zoo")
    _video(conn, "yt_volcano", science, "Volcano")
    pid = _kid(conn, "lia")
    kid = _as_kid(app, pid)
    page = kid.get("/channels").text
    assert f'/channel/{animals}"' in page and f'/channel/{bedtime}"' not in page

    _video(conn, "yt_moon", bedtime, "Goodnight moon")
    assert f'/channel/{bedtime}"' in kid.get("/channels").text


def test_freed_videos_stay_listed_for_kids(app):
    conn = _conn(app)
    ch = ChannelRepository(conn).create("Facts")
    _video(conn, "yt_facts", ch, "Five facts")
    VideoRepository(conn).mark_evicted("yt_facts")
    kid = _as_kid(app, _kid(conn, "sol"))
    for path in ("/", f"/channel/{ch}", "/search?q=facts"):
        page = kid.get(path).text
        assert 'href="/watch/yt_facts"' in page, path
    assert "Takes a moment to start" in kid.get("/").text


def test_kid_makes_a_channel_from_the_video_edit_page(app):
    conn = _conn(app)
    songs = ChannelRepository(conn).create("Songs")
    _video(conn, "yt_song", songs, "A song")
    pid = _kid(conn, "cy")
    other = _kid(conn, "di")
    ChannelRepository(conn).create("Di's", owner_profile_id=other)
    kid = _as_kid(app, pid)
    assert 'name="new_channel"' in kid.get("/video/yt_song/edit").text

    resp = kid.post("/video/yt_song/edit", data={"title": "", "new_channel": " Best  songs "})
    assert resp.status_code == 302
    mine = ChannelRepository(conn).get_by_name("Best songs")
    assert mine is not None and mine.owner_profile_id == pid
    assert "A song" in kid.get(f"/channel/{mine.id}").text

    resp = kid.post("/video/yt_song/edit", data={"title": "", "channel_" + str(mine.id): "1",
                                                 "new_channel": "Di's"})
    assert resp.status_code == 400 and "already used" in resp.text


def test_kid_deletes_only_their_own_channel(app):
    conn = _conn(app)
    channels = ChannelRepository(conn)
    pid, other = _kid(conn, "eve"), _kid(conn, "fay")
    mine = channels.create("Eve's", owner_profile_id=pid)
    theirs = channels.create("Fay's", owner_profile_id=other)
    ours = channels.create("Cartoons")
    _video(conn, "yt_upload", mine, "My film")
    kid = _as_kid(app, pid)

    page = kid.get(f"/channel/{mine}/delete").text
    assert "Delete Eve&#39;s?" in page and "go to a grown-up" in page
    for cid in (theirs, ours):
        assert kid.get(f"/channel/{cid}/delete").headers["location"] == "/"
        kid.post(f"/channel/{cid}/delete")
        assert channels.get(cid) is not None

    assert kid.post(f"/channel/{mine}/delete").headers["location"] == "/"
    assert channels.get(mine) is None
    video = VideoRepository(conn).get("yt_upload")
    assert video is not None and video.channel_id is None


def test_kid_renaming_to_a_taken_name_says_so(app):
    conn = _conn(app)
    pid = _kid(conn, "gus")
    mine = ChannelRepository(conn).create("Gus's", owner_profile_id=pid)
    ChannelRepository(conn).create("Cartoons")
    kid = _as_kid(app, pid)
    resp = kid.post(f"/channel/{mine}/edit", data={"name": "Cartoons", "color": "#123abc"})
    assert resp.status_code == 400 and "already used" in resp.text
    assert ChannelRepository(conn).get(mine).name == "Gus's"
    kid.post(f"/channel/{mine}/edit", data={"name": " Gus   films ", "color": "#123abc"})
    assert ChannelRepository(conn).get(mine).name == "Gus films"
