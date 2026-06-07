"""Route-level tests — HTTP status codes, auth enforcement, redirects."""

import pytest


class TestParentAuth:
    def test_login_page_returns_200(self, client):
        resp = client.get("/parent/login")
        assert resp.status_code == 200

    def test_login_wrong_password_shows_error(self, client, app):
        from app.services.auth import AuthService
        auth = AuthService(app.state.config)
        auth.set_password("correct")
        resp = client.post("/parent/login", data={"password": "wrong"})
        assert resp.status_code == 200
        assert b"Incorrect password" in resp.content

    def test_login_correct_password_redirects(self, authed_client):
        # authed_client fixture already logged in successfully
        resp = authed_client.get("/parent/")
        assert resp.status_code == 200

    def test_unauthenticated_parent_redirects_to_login(self, client, app):
        # Set a password so it's not first-run
        from app.services.auth import AuthService
        auth = AuthService(app.state.config)
        auth.set_password("testpass")
        resp = client.get("/parent/settings")
        assert resp.status_code == 302
        assert "/parent/login" in resp.headers["location"]

    def test_parent_login_clears_child_profile(self, client, app):
        """Regression: logging in as parent while a kid profile is
        active in the session must clear the profile so get_viewer
        resolves as parent. Previously the viewer lookup preferred
        profile_id and the parent would be bounced out of every
        /parent/ route immediately after successful auth."""
        from app.services.auth import AuthService
        from app.dependencies import get_db
        from app.repositories import ProfileRepository
        from app.models import Profile
        AuthService(app.state.config).set_password("pw")
        conn = next(app.dependency_overrides[get_db]())
        pid = ProfileRepository(conn).create(Profile(
            name="alice", display_name="Alice", pin="",
            allowed_channel_ids=[],
        ))
        # Simulate a kid being logged in in this session
        client.post("/profiles/select", data={"profile_id": str(pid)})

        # Parent logs in: should succeed AND land on the parent dashboard.
        resp = client.post("/parent/login", data={"password": "pw"})
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/"

        # Follow-up request to /parent/ should NOT redirect back to login
        # (which would mean the viewer is still resolving as a kid).
        resp2 = client.get("/parent/")
        assert resp2.status_code == 200


class TestParentSettings:
    def test_settings_page_loads(self, authed_client):
        resp = authed_client.get("/parent/settings")
        assert resp.status_code == 200
        assert b"Settings" in resp.content


class TestParentChannels:
    def test_channels_page_loads(self, authed_client):
        resp = authed_client.get("/parent/channels/")
        assert resp.status_code == 200


class TestAnonymousKidRedirect:
    """Anonymous visitors landing on kid-facing URLs get bounced to
    /profiles so they can pick a kid (or auto-select the only one).
    Previously the kid flow just rendered as anonymous with no
    comment form, no reactions, and no login link, which left users
    stuck with no obvious way to proceed.
    """

    def _setup_app(self, app):
        from app.services.auth import AuthService
        auth = AuthService(app.state.config)
        auth.set_password("testpass")

    def test_anonymous_home_redirects_to_profiles(self, client, app):
        self._setup_app(app)
        resp = client.get("/")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/profiles"

    def test_anonymous_watch_redirects_to_profiles(self, client, app):
        self._setup_app(app)
        resp = client.get("/watch/vidany0000001")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/profiles"

    def test_anonymous_channel_redirects_to_profiles(self, client, app):
        self._setup_app(app)
        resp = client.get("/channel/1")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/profiles"

    def test_anonymous_profiles_does_not_redirect(self, client, app):
        """The /profiles page itself must not get caught by the
        middleware — that would be an infinite redirect loop."""
        self._setup_app(app)
        resp = client.get("/profiles")
        assert resp.status_code == 200

    def test_anonymous_parent_routes_pass_through(self, client, app):
        self._setup_app(app)
        resp = client.get("/parent/login")
        assert resp.status_code == 200

    def test_anonymous_media_pass_through(self, client, app):
        """Static and media endpoints must not be caught — the kid
        player fetches /media/video/... and /media/thumb/... and must
        not get a 302 in response."""
        self._setup_app(app)
        resp = client.get("/media/thumb/nonexistent1")
        assert resp.status_code == 200

    def test_parent_session_does_not_redirect(self, authed_client, app):
        """A logged-in parent hitting a kid URL sees the page (as a
        parent viewer) rather than getting bounced to /profiles."""
        resp = authed_client.get("/")
        assert resp.status_code in (200, 302)
        # If it's a 302, it shouldn't be going to /profiles
        if resp.status_code == 302:
            assert resp.headers["location"] != "/profiles"

    def test_kid_session_does_not_redirect(self, client, app):
        """A logged-in kid hitting a kid URL sees the page directly."""
        self._setup_app(app)
        # Create a kid profile and log in
        from app.dependencies import get_db
        from app.repositories import ProfileRepository
        from app.models import Profile
        conn = next(app.dependency_overrides[get_db]())
        pid = ProfileRepository(conn).create(
            Profile(name="alice", display_name="Alice", pin=""))
        client.post("/profiles/select", data={"profile_id": str(pid)})
        resp = client.get("/")
        assert resp.status_code == 200

    def test_post_to_kid_route_is_not_redirected(self, client, app):
        """POST requests must not be intercepted — only GET.
        Otherwise /api/log and /api/react would break."""
        self._setup_app(app)
        resp = client.post("/api/log", data={"event": "test", "video_id": "x"})
        # Should reach the route (204) not bounce (302)
        assert resp.status_code != 302


class TestKidRoutes:
    def test_profiles_page_empty_state(self, client, app):
        # Set password so it's not first-run (avoids redirect to setup)
        from app.services.auth import AuthService
        auth = AuthService(app.state.config)
        auth.set_password("testpass")
        resp = client.get("/profiles")
        # With no profiles, the picker now renders a helpful
        # empty-state page with a link to /parent/login so the parent
        # can sign in and create a kid profile. (It used to redirect
        # to /, which caused an infinite loop with the
        # anonymous_kid_redirect middleware.)
        assert resp.status_code == 200
        assert b"No kid profiles yet" in resp.content
        assert b"/parent/login" in resp.content


class TestApiRoutes:
    def test_log_event_returns_204(self, client):
        resp = client.post("/api/log", data={"event": "test", "video_id": "abc12345678"})
        assert resp.status_code == 204


class TestMediaRoutes:
    def test_thumbnail_nonexistent_returns_placeholder(self, authed_client):
        # Parent viewer gets SVG placeholder; anonymous/kid gets transparent pixel
        resp = authed_client.get("/media/thumb/nonexistent1")
        assert resp.status_code == 200
        assert "svg" in resp.headers.get("content-type", "")

    def test_thumbnail_nonexistent_kid_returns_pixel(self, client):
        resp = client.get("/media/thumb/nonexistent1")
        assert resp.status_code == 200
        assert "png" in resp.headers.get("content-type", "")


class TestSubtitleNormalization:
    """VTT cue settings (align:start position:0%) from YouTube auto-
    captions cause the native HTML5 player to render text off-center
    or cropped to the left. The serve_subtitle route strips cue
    settings on the fly so the browser falls back to centered default.
    """

    def test_normalize_vtt_strips_cue_settings(self):
        from app.features.media.router import _normalize_vtt
        raw = (
            "WEBVTT\n"
            "Kind: captions\n"
            "Language: en\n"
            "\n"
            "00:00:00.120 --> 00:00:02.550 align:start position:0%\n"
            "hey folks\n"
            "\n"
            "00:00:02.560 --> 00:00:05.749 align:start position:0%\n"
            "welcome back\n"
        )
        out = _normalize_vtt(raw)
        assert "align:start" not in out
        assert "position:0%" not in out
        # Header, timestamps, and cue text all preserved
        assert "WEBVTT" in out
        assert "Kind: captions" in out
        assert "00:00:00.120 --> 00:00:02.550" in out
        assert "00:00:02.560 --> 00:00:05.749" in out
        assert "hey folks" in out
        assert "welcome back" in out

    def test_normalize_vtt_preserves_clean_cues(self):
        """A VTT without cue settings should round-trip unchanged."""
        from app.features.media.router import _normalize_vtt
        raw = (
            "WEBVTT\n"
            "\n"
            "00:00:00.000 --> 00:00:02.000\n"
            "Hello world\n"
        )
        out = _normalize_vtt(raw)
        assert "00:00:00.000 --> 00:00:02.000" in out
        assert "Hello world" in out

    def test_serve_subtitle_returns_normalized_vtt(self, authed_client, app, tmp_path):
        """End-to-end: place a VTT with cue settings in the videos dir,
        request it via /media/subs/, verify the response strips them."""
        data_dir = app.state.config.data_dir
        vdir = data_dir / "videos" / "vidsubtest01"
        vdir.mkdir(parents=True, exist_ok=True)
        (vdir / "video.en.vtt").write_text(
            "WEBVTT\n\n"
            "00:00:00.000 --> 00:00:02.000 align:start position:0%\n"
            "Test caption\n",
            encoding="utf-8",
        )
        resp = authed_client.get("/media/subs/vidsubtest01/video.en.vtt")
        assert resp.status_code == 200
        assert "text/vtt" in resp.headers.get("content-type", "")
        body = resp.text
        assert "align:start" not in body
        assert "position:0%" not in body
        assert "Test caption" in body


class TestWatchPagePlayer:
    def test_watch_page_has_poster_and_playsinline(self, authed_client, app):
        """The watch page shows a poster thumbnail and uses playsinline
        (no autoplay — kid taps play when ready)."""
        from app.dependencies import get_db
        from app.repositories import VideoRepository, ChannelRepository
        from app.models import Video
        conn = next(app.dependency_overrides[get_db]())
        cid = ChannelRepository(conn).create("Family")
        VideoRepository(conn).insert(Video(
            video_id="vidautopl001",
            title="Test",
            original_title="Test",
            channel_id=cid,
            status="active",
            download_status="ready",
        ))
        resp = authed_client.get("/watch/vidautopl001")
        assert resp.status_code == 200
        body = resp.content
        assert b"autoplay" not in body
        assert b"playsinline" in body
        assert b"poster=" in body


class TestMultiSourceAddConfirm:
    """The parent add/confirm route is platform-agnostic after the
    multi-source refactor. Video IDs that aren't YouTube-shaped (11
    chars from [A-Za-z0-9_-]) must still land in the DB, and the
    stored video_id must be the composite `{extractor}_{raw_id}`
    form so filesystem paths stay safe across platforms.
    """

    def _confirm_payload(self, vid, extractor, original_url):
        return {
            "url_type": "video",
            "source_url": original_url,
            "source_title": "Test",
            "resolution": "360p",
            "channel_id": "",
            "new_channel_name": "",
            "video_ids": vid,
            f"extractor_{vid}": extractor,
            f"original_url_{vid}": original_url,
            f"original_title_{vid}": "Title",
            f"channel_name_{vid}": "Channel",
            f"duration_{vid}": "10",
            f"upload_date_{vid}": "",
            f"view_count_{vid}": "0",
            f"thumb_{vid}": "",
            f"original_desc_{vid}": "",
            f"title_{vid}": "Title",
            f"desc_{vid}": "desc",
            f"watched_{vid}": "1",
        }

    def test_vimeo_id_accepted(self, authed_client, app, monkeypatch):
        """A Vimeo-shaped numeric ID (not 11 chars, not [A-Za-z0-9_-])
        must NOT be dropped by the form-level regex any more.
        """
        # Stop the background download thread from actually trying
        # to fetch Vimeo — replace it with a no-op.
        from app.services import content as content_mod
        monkeypatch.setattr(
            content_mod.ContentService, "_start_download",
            lambda self, vid, url, res: None,
        )
        payload = self._confirm_payload(
            "123456789", "vimeo", "https://vimeo.com/123456789",
        )
        resp = authed_client.post("/parent/add/confirm", data=payload)
        assert resp.status_code == 200
        assert b"Added 1 video" in resp.content

        from app.dependencies import get_db
        from app.repositories import VideoRepository
        conn = next(app.dependency_overrides[get_db]())
        v = VideoRepository(conn).get("vimeo_123456789")
        assert v is not None, "video row should be keyed on the composite id"
        assert v.extractor == "vimeo"
        assert v.original_url == "https://vimeo.com/123456789"

    def test_youtube_id_still_accepted(self, authed_client, app, monkeypatch):
        """The YouTube path still works end-to-end (regression check)."""
        from app.services import content as content_mod
        monkeypatch.setattr(
            content_mod.ContentService, "_start_download",
            lambda self, vid, url, res: None,
        )
        payload = self._confirm_payload(
            "dQw4w9WgXcQ", "youtube",
            "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        )
        resp = authed_client.post("/parent/add/confirm", data=payload)
        assert resp.status_code == 200
        assert b"Added 1 video" in resp.content

        from app.dependencies import get_db
        from app.repositories import VideoRepository
        conn = next(app.dependency_overrides[get_db]())
        v = VideoRepository(conn).get("youtube_dQw4w9WgXcQ")
        assert v is not None
        assert v.extractor == "youtube"
        assert v.original_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    def test_unsafe_video_id_rejected(self, authed_client, app, monkeypatch):
        """Path-traversal via video_id must never reach the
        filesystem: a form field containing `..` or `/` is dropped
        at the route layer before any DB write.
        """
        from app.services import content as content_mod
        monkeypatch.setattr(
            content_mod.ContentService, "_start_download",
            lambda self, vid, url, res: None,
        )
        payload = self._confirm_payload(
            "../../etc/passwd", "youtube",
            "https://example.com/hack",
        )
        # The `watched_...` key needs to match the attacker-supplied
        # video_ids value, which the helper already does.
        resp = authed_client.post("/parent/add/confirm", data=payload)
        assert resp.status_code == 200
        # Zero videos added — the id never passed validation.
        assert b"Added 0 video" in resp.content

        from app.dependencies import get_db
        from app.repositories import VideoRepository
        conn = next(app.dependency_overrides[get_db]())
        # No composite id possible for this input
        assert VideoRepository(conn).get("youtube_..-..-etc-passwd") is None


class TestTagInlineEditXHR:
    """The watch-page tag editor posts via XHR (X-Requested-With) so the
    chips update in place — a full redirect/reload would re-create the
    <video> element and restart playback. A plain <form> submit (JS off)
    still gets the PRG redirect. Regression for the tag-add-restarts-the-
    video bug."""

    def _kid_with_video(self, client, app):
        from app.dependencies import get_db
        from app.repositories import (
            ProfileRepository, VideoRepository, ChannelRepository,
        )
        from app.models import Profile
        from app.models.video import Video
        conn = next(app.dependency_overrides[get_db]())
        pid = ProfileRepository(conn).create(Profile(
            name="alice", display_name="Alice", pin="", allowed_channel_ids=[]))
        cid = ChannelRepository(conn).create("Family")  # kid sees all channels
        VideoRepository(conn).insert(Video(
            video_id="youtube_v1", title="V", original_title="V",
            status="active", download_status="ready", channel_id=cid))
        client.post("/profiles/select", data={"profile_id": str(pid)})
        return "youtube_v1"

    def test_xhr_add_returns_json_with_id(self, client, app):
        vid = self._kid_with_video(client, app)
        resp = client.post(f"/video/{vid}/tags/add", data={"tag": "volcano"},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "volcano"
        assert isinstance(body["id"], int)

    def test_xhr_remove_returns_204(self, client, app):
        vid = self._kid_with_video(client, app)
        add = client.post(f"/video/{vid}/tags/add", data={"tag": "x"},
                          headers={"X-Requested-With": "XMLHttpRequest"})
        tid = add.json()["id"]
        resp = client.post(f"/video/{vid}/tags/remove",
                           data={"tag_id": str(tid)},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 204

    def test_plain_form_add_still_redirects(self, client, app):
        """No X-Requested-With → PRG redirect fallback (works with JS off)."""
        vid = self._kid_with_video(client, app)
        resp = client.post(f"/video/{vid}/tags/add", data={"tag": "y"})
        assert resp.status_code == 302
        assert "#tags" in resp.headers["location"]


class TestCommentInlineEditXHR:
    """Posting a comment/reply via XHR returns the rendered node (so the
    watch page inserts it in place instead of reloading and restarting
    the video). Plain form posts still redirect. Regression for the
    comment-post-restarts-the-video issue."""

    @pytest.fixture(autouse=True)
    def _reset_rate_limiters(self):
        # The comment rate limiter is module-level state keyed on
        # profile_id; profile ids repeat across fresh test DBs, so clear
        # it before each test (mirrors tests/test_comments.py).
        from app.services.comments import (
            _KID_RATE_LIMITER, _PARENT_RATE_LIMITER,
        )
        _KID_RATE_LIMITER.reset()
        _PARENT_RATE_LIMITER.reset()
        yield

    def _kid_with_video(self, client, app):
        from app.dependencies import get_db
        from app.repositories import (
            ProfileRepository, VideoRepository, ChannelRepository,
        )
        from app.models import Profile
        from app.models.video import Video
        conn = next(app.dependency_overrides[get_db]())
        pid = ProfileRepository(conn).create(Profile(
            name="bob", display_name="Bob", pin="", allowed_channel_ids=[]))
        cid = ChannelRepository(conn).create("Family")
        VideoRepository(conn).insert(Video(
            video_id="youtube_c1", title="V", original_title="V",
            status="active", download_status="ready", channel_id=cid))
        client.post("/profiles/select", data={"profile_id": str(pid)})
        return "youtube_c1"

    def test_xhr_comment_returns_rendered_node(self, client, app):
        vid = self._kid_with_video(client, app)
        resp = client.post(f"/watch/{vid}/comment",
                           data={"body": "first post"},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 200
        assert b"first post" in resp.content
        assert b"data-comment-id=" in resp.content

    def test_xhr_reply_marks_parent(self, client, app):
        vid = self._kid_with_video(client, app)
        top = client.post(f"/watch/{vid}/comment", data={"body": "top"},
                          headers={"X-Requested-With": "XMLHttpRequest"})
        import re
        cid = re.search(rb'data-comment-id="(\d+)"', top.content).group(1).decode()
        reply = client.post(f"/watch/{vid}/comment",
                            data={"body": "a reply", "parent_comment_id": cid},
                            headers={"X-Requested-With": "XMLHttpRequest"})
        assert reply.status_code == 200
        assert f'data-parent-id="{cid}"'.encode() in reply.content

    def test_xhr_empty_body_returns_204(self, client, app):
        vid = self._kid_with_video(client, app)
        resp = client.post(f"/watch/{vid}/comment", data={"body": "   "},
                           headers={"X-Requested-With": "XMLHttpRequest"})
        assert resp.status_code == 204

    def test_plain_form_comment_still_redirects(self, client, app):
        vid = self._kid_with_video(client, app)
        resp = client.post(f"/watch/{vid}/comment", data={"body": "hi"})
        assert resp.status_code == 302
        assert "#comments" in resp.headers["location"]
