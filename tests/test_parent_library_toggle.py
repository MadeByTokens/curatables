"""Parent library-mode toggle — edit form + list badge."""

from app.dependencies import get_db
from app.models import Video
from app.repositories import VideoRepository


def _insert_video(app, video_id, *, keep_forever=False, download_status="ready"):
    conn = next(app.dependency_overrides[get_db]())
    repo = VideoRepository(conn)
    repo.insert(Video(
        video_id=video_id, title=f"v {video_id}",
        original_title=f"v {video_id}",
        download_status="pending",
    ))
    conn.execute(
        "UPDATE videos SET download_status=?, keep_forever=? WHERE video_id=?",
        (download_status, 1 if keep_forever else 0, video_id),
    )
    conn.commit()


class TestLibraryToggle:
    def test_edit_form_renders_unchecked_by_default(self, app, authed_client):
        _insert_video(app, "yt_a")
        resp = authed_client.get("/parent/content/yt_a/edit")
        assert resp.status_code == 200
        html = resp.text
        assert 'name="keep_forever"' in html
        # Not currently checked:
        assert 'name="keep_forever" value="1"\n             checked' not in html
        # But the checkbox is present:
        assert "Keep forever" in html

    def test_edit_form_renders_checked_when_pinned(self, app, authed_client):
        _insert_video(app, "yt_pinned", keep_forever=True)
        resp = authed_client.get("/parent/content/yt_pinned/edit")
        assert resp.status_code == 200
        assert "checked" in resp.text
        assert "Keep forever" in resp.text

    def test_post_edit_sets_keep_forever(self, app, authed_client):
        _insert_video(app, "yt_flip")
        resp = authed_client.post("/parent/content/yt_flip/edit", data={
            "title": "new title",
            "description": "",
            "channel_id": "",
            "new_channel_name": "",
            "resolution": "720p",
            "keep_forever": "1",
        })
        assert resp.status_code == 200
        conn = next(app.dependency_overrides[get_db]())
        v = VideoRepository(conn).get("yt_flip")
        assert v.keep_forever is True

    def test_post_edit_clears_keep_forever_when_unchecked(self, app, authed_client):
        _insert_video(app, "yt_unpin", keep_forever=True)
        # Unchecked checkbox is simply absent from the form body.
        resp = authed_client.post("/parent/content/yt_unpin/edit", data={
            "title": "still titled",
            "description": "",
            "channel_id": "",
            "new_channel_name": "",
            "resolution": "720p",
            # keep_forever omitted
        })
        assert resp.status_code == 200
        conn = next(app.dependency_overrides[get_db]())
        v = VideoRepository(conn).get("yt_unpin")
        assert v.keep_forever is False

    def test_content_list_shows_library_badge(self, app, authed_client):
        _insert_video(app, "yt_pin1", keep_forever=True)
        _insert_video(app, "yt_plain")
        resp = authed_client.get("/parent/content")
        assert resp.status_code == 200
        assert "library" in resp.text  # badge label

    def test_content_list_shows_evicted_state(self, app, authed_client):
        _insert_video(app, "yt_evicted", download_status="evicted")
        resp = authed_client.get("/parent/content")
        assert resp.status_code == 200
        assert "evicted" in resp.text
