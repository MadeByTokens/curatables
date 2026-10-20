"""A kid saves a video onto their device, or hides one they don't like
and the parent is told."""

from fastapi.testclient import TestClient

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.repositories.override_repo import ProfileVideoOverrideRepository
from app.repositories.tag_repo import TagRepository


def _setup(app, **flags):
    conn = next(app.dependency_overrides[get_db]())
    ch = ChannelRepository(conn).create("Trains")
    videos = VideoRepository(conn)
    for vid, title, key in (("yt_a", "Steam train", 1), ("yt_b", "Diesel train", 2),
                            ("yt_c", "Electric train", 3)):
        videos.insert(Video(video_id=vid, title=title, original_title=title, channel_id=ch,
                            status="active", download_status="ready", storage_mode="uploaded"))
        videos.update(vid, episode_key=key)
    vdir = app.state.config.media_dir / "uploads" / "yt_a"
    vdir.mkdir(parents=True)
    (vdir / "video.mp4").write_bytes(b"not really a video")
    profiles = ProfileRepository(conn)
    pid = profiles.create(Profile(name="sam", display_name="Sam", **flags))
    sib = profiles.create(Profile(name="ivy", display_name="Ivy"))
    kid = TestClient(app, follow_redirects=False)
    kid.post("/profiles/select", data={"profile_id": pid})
    sibling = TestClient(app, follow_redirects=False)
    sibling.post("/profiles/select", data={"profile_id": sib})
    return conn, kid, sibling, pid


def test_download_saves_the_file_under_the_kids_title(app):
    conn, kid, _, pid = _setup(app)
    ProfileVideoOverrideRepository(conn).upsert(pid, "yt_a", title='Choo: "choo"')
    page = kid.get("/watch/yt_a").text
    assert 'href="/media/video/yt_a?download=1" download' in page
    assert "nodownload" not in page
    resp = kid.get("/media/video/yt_a?download=1")
    assert resp.status_code == 200
    assert resp.headers["content-disposition"] == "attachment; filename*=utf-8''Choo%20choo.mp4"
    # Playing doesn't download.
    assert "content-disposition" not in kid.get("/media/video/yt_a").headers


def test_download_switched_off(app):
    _, kid, _, _ = _setup(app, allow_download=False)
    page = kid.get("/watch/yt_a").text
    assert "?download=1" not in page
    assert "nodownload" in page
    assert kid.get("/media/video/yt_a?download=1").status_code == 403


def test_hidden_video_leaves_only_that_kids_lists(app):
    conn, kid, sibling, pid = _setup(app)
    TagRepository(conn).add_to_video(pid, "yt_b", TagRepository(conn).get_or_create("fast"))
    assert 'action="/video/yt_b/hide"' in kid.get("/watch/yt_b").text
    resp = kid.post("/video/yt_b/hide")
    assert resp.headers["location"] == "/?hid=yt_b"
    home = kid.get(resp.headers["location"]).text
    assert "A grown-up will be told" in home
    assert 'action="/video/yt_b/unhide"' in home
    assert "Diesel train" not in home
    assert "Diesel train" not in kid.get("/search?q=diesel").text
    assert kid.get("/tags").text.count("fast") == 0
    assert kid.get("/watch/yt_b").status_code == 302
    assert kid.get("/media/video/yt_b").status_code == 404
    # The episode after the one before it skips the hidden one.
    assert 'href="/watch/yt_c" class="end-btn"' in kid.get("/watch/yt_a").text
    assert "Diesel train" in sibling.get("/").text

    kid.post("/video/yt_b/unhide")
    assert "Diesel train" in kid.get("/").text


def test_parent_is_told_and_decides(app, authed_client):
    _, kid, _, pid = _setup(app)
    kid.post("/video/yt_b/hide")
    dash = authed_client.get("/parent/").text
    assert "1\n        video a kid hid" in dash
    assert 'href="/parent/content/yt_b">Diesel train</a> (Sam)' in dash
    assert "Diesel train" in authed_client.get("/parent/content?status=kid_hidden").text

    detail = authed_client.get("/parent/content/yt_b").text
    assert "Sam hid this video" in detail and "Delete video" in detail
    authed_client.post(f"/parent/content/yt_b/kid-hidden/{pid}", data={"action": "keep"})
    assert "video a kid hid" not in authed_client.get("/parent/").text
    detail = authed_client.get("/parent/content/yt_b").text
    assert "it stays hidden from them" in detail and "Delete video" not in detail
    assert "Diesel train" not in kid.get("/").text

    authed_client.post(f"/parent/content/yt_b/kid-hidden/{pid}", data={"action": "show"})
    assert "Diesel train" in kid.get("/").text

    kid.post("/video/yt_b/hide")
    authed_client.post("/parent/content/yt_b/delete")
    assert "video a kid hid" not in authed_client.get("/parent/").text


def test_hide_switched_off(app):
    _, kid, _, _ = _setup(app, allow_hide=False)
    assert "/hide" not in kid.get("/watch/yt_b").text
    assert kid.post("/video/yt_b/hide").status_code == 403
    assert "Diesel train" in kid.get("/").text


def test_a_video_deleted_while_its_player_loads_is_not_found(app, monkeypatch):
    """A parent deletes a video while a kid's player is asking for it:
    its folder goes between any check and the listing."""
    import pathlib
    _, kid, _, _ = _setup(app)
    real = pathlib.Path.iterdir

    def gone(self):
        if self.name in ("yt_a", "yt_b"):
            raise FileNotFoundError(str(self))
        return real(self)

    monkeypatch.setattr(pathlib.Path, "iterdir", gone)
    assert kid.get("/media/video/yt_a").status_code == 404
    assert kid.get("/media/video/yt_a?download=1").status_code == 404
    assert kid.get("/media/subs/yt_a").json() == []
