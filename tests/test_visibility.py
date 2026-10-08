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


def test_kid_nav_hides_non_whitelisted_channels(app):
    conn = _conn(app)
    channels = ChannelRepository(conn)
    allowed = channels.create("Trains")
    channels.create("Secret Grownup Channel")
    _video(conn, "yt_train", allowed, "Train clip")
    pid = _kid(conn, "dan", allowed=[allowed])

    kid = _as_kid(app, pid)
    home = kid.get("/")
    assert "Trains" in home.text
    assert "Secret Grownup Channel" not in home.text


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


def test_api_errors_stay_json(client):
    resp = client.post("/api/react", data={})
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
