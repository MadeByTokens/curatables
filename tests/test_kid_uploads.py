"""Tests for kid uploads and kid-created channels.

Covers fresh-schema column presence, ChannelRepository visibility
queries, ContentService kid-aware listing, require_child dependency,
the kid_uploads routes (GET upload page, POST /upload, POST
/upload/new-channel), max_kid_upload_bytes setting, and parent stats
friendly event labels.
"""

import io
import json
from unittest.mock import patch

import pytest

from app.db.schema import init_schema
from app.models import Profile, Video
from app.repositories import (
    ChannelRepository, ProfileRepository, VideoRepository, EventRepository,
)
from app.repositories.reaction_repo import ReactionRepository
from app.services.channels import ChannelService
from app.services.media_probe import ProbeResult, UnsupportedCodec
from app.models.viewer import ViewerContext


_GB = 1_073_741_824


def _make_video(video_id, title="Test", channel_id=None,
                status="active", download_status="ready",
                storage_mode="cache"):
    return Video(
        video_id=video_id, title=title, original_title=title,
        channel_id=channel_id, status=status, download_status=download_status,
        storage_mode=storage_mode,
    )


def _make_kid_profile(db, name, display_name=None,
                      allowed_channel_ids=None):
    repo = ProfileRepository(db)
    profile = Profile(
        name=name,
        display_name=display_name or name.title(),
        pin="",
        allowed_channel_ids=allowed_channel_ids or [],
    )
    return repo.create(profile)


# ---------------------------------------------------------------------------
# Schema coverage — fresh-database sanity
# ---------------------------------------------------------------------------

class TestSchema:
    def test_fresh_db_has_owner_column(self, db):
        cols = db.execute("PRAGMA table_info(channels)").fetchall()
        names = {c["name"] for c in cols}
        assert "owner_profile_id" in names

    def test_fresh_db_has_owner_index(self, db):
        indexes = {r["name"] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='channels'"
        ).fetchall()}
        assert "idx_channels_owner" in indexes

    def test_init_schema_is_idempotent(self, db):
        # Running init_schema twice on the same connection must not error
        # and must not duplicate the column.
        init_schema(db)
        cols = db.execute("PRAGMA table_info(channels)").fetchall()
        names = [c["name"] for c in cols]
        assert names.count("owner_profile_id") == 1


# ---------------------------------------------------------------------------
# ChannelRepository visibility queries
# ---------------------------------------------------------------------------

class TestChannelVisibility:
    def test_create_with_owner_profile_id(self, db):
        # Need a profile first so the FK is valid
        alice = _make_kid_profile(db, "alice")
        repo = ChannelRepository(db)
        cid = repo.create("Alice's Art", owner_profile_id=alice)
        channel = repo.get(cid)
        assert channel is not None
        assert channel.owner_profile_id == alice

    def test_create_without_owner(self, db):
        repo = ChannelRepository(db)
        cid = repo.create("Family")
        channel = repo.get(cid)
        assert channel is not None
        assert channel.owner_profile_id is None

    def test_list_visible_to_unrestricted(self, db):
        alice = _make_kid_profile(db, "alice")
        bob = _make_kid_profile(db, "bob")
        repo = ChannelRepository(db)
        family = repo.create("Family")
        science = repo.create("Science")
        alice_owned = repo.create("Alice's Art", owner_profile_id=alice)
        bob_owned = repo.create("Bob's Films", owner_profile_id=bob)

        visible = set(repo.list_visible_to(alice, whitelist_ids=None))
        assert family in visible
        assert science in visible
        assert alice_owned in visible
        assert bob_owned not in visible  # sibling-owned is hidden

    def test_list_visible_to_restricted(self, db):
        alice = _make_kid_profile(db, "alice")
        bob = _make_kid_profile(db, "bob")
        repo = ChannelRepository(db)
        family = repo.create("Family")
        science = repo.create("Science")
        cartoons = repo.create("Cartoons")
        alice_owned = repo.create("Alice's Art", owner_profile_id=alice)
        bob_owned = repo.create("Bob's Films", owner_profile_id=bob)

        # Alice is restricted to Family + Science
        visible = set(repo.list_visible_to(alice, whitelist_ids=[family, science]))
        assert family in visible
        assert science in visible
        assert cartoons not in visible  # not whitelisted
        assert alice_owned in visible    # own owned is always visible
        assert bob_owned not in visible   # sibling-owned still hidden

    def test_list_visible_to_whitelisted_sibling_channel_is_honored(self, db):
        """If the parent explicitly whitelists a sibling-owned channel
        for this kid, it should still be visible to them."""
        alice = _make_kid_profile(db, "alice")
        bob = _make_kid_profile(db, "bob")
        repo = ChannelRepository(db)
        bob_owned = repo.create("Bob's Films", owner_profile_id=bob)
        # Parent explicitly shares Bob's channel with Alice
        visible = set(repo.list_visible_to(alice, whitelist_ids=[bob_owned]))
        assert bob_owned in visible

    def test_list_owned_by(self, db):
        alice = _make_kid_profile(db, "alice")
        bob = _make_kid_profile(db, "bob")
        repo = ChannelRepository(db)
        repo.create("Family")
        a1 = repo.create("Alice's Art", owner_profile_id=alice)
        a2 = repo.create("Alice's Music", owner_profile_id=alice)
        repo.create("Bob's Films", owner_profile_id=bob)

        alice_channels = repo.list_owned_by(alice)
        ids = {c.id for c in alice_channels}
        assert ids == {a1, a2}
        assert repo.count_owned_by(alice) == 2
        assert repo.count_owned_by(bob) == 1


# ---------------------------------------------------------------------------
# ContentService kid visibility
# ---------------------------------------------------------------------------

class TestContentServiceKidVisibility:
    def _setup(self, db):
        alice = _make_kid_profile(db, "alice")
        bob = _make_kid_profile(db, "bob")
        ch_repo = ChannelRepository(db)
        vid_repo = VideoRepository(db)
        family = ch_repo.create("Family")
        alice_ch = ch_repo.create("Alice's Art", owner_profile_id=alice)
        bob_ch = ch_repo.create("Bob's Films", owner_profile_id=bob)

        vid_repo.insert(_make_video("fam00000001", title="Family Trip",
                                    channel_id=family))
        vid_repo.insert(_make_video("alice0000001", title="Alice Drawing",
                                    channel_id=alice_ch, storage_mode="uploaded"))
        vid_repo.insert(_make_video("bob000000001", title="Bob Film",
                                    channel_id=bob_ch, storage_mode="uploaded"))

        from app.services.content import ContentService
        from app.repositories.source_repo import SourceRepository
        src_repo = SourceRepository(db)
        # The service takes many deps but for visibility queries we only
        # need the three repos. Stub the rest with None.
        service = ContentService(
            video_repo=vid_repo, source_repo=src_repo, channel_repo=ch_repo,
            source=None, thumbnails=None, storage=None, config=None,
        )
        return service, alice, bob, family, alice_ch, bob_ch

    def _viewer(self, profile_id, allowed=None):
        return ViewerContext(
            viewer_type="child",
            profile_id=profile_id,
            profile_name="kid",
            display_name="Kid",
            allowed_channel_ids=allowed,
            search_mode="enabled",
            theme="base",
        )

    def test_list_for_viewer_shows_own_and_parent_hides_sibling(self, db):
        service, alice, bob, fam, alice_ch, bob_ch = self._setup(db)
        videos, _ = service.list_for_viewer(self._viewer(alice))
        ids = {v.video_id for v in videos}
        assert "fam00000001" in ids
        assert "alice0000001" in ids
        assert "bob000000001" not in ids

    def test_search_for_viewer_hides_sibling_content(self, db):
        service, alice, bob, fam, alice_ch, bob_ch = self._setup(db)
        results = service.search_for_viewer("i", self._viewer(alice))
        ids = {v.video_id for v in results}
        assert "bob000000001" not in ids

    def test_get_video_for_viewer_refuses_sibling_video(self, db):
        from app.services.content import VideoNotFound
        service, alice, bob, fam, alice_ch, bob_ch = self._setup(db)
        # Alice tries to fetch Bob's video directly
        with pytest.raises(VideoNotFound):
            service.get_video_for_viewer("bob000000001", self._viewer(alice))
        # Bob can fetch it
        video = service.get_video_for_viewer("bob000000001", self._viewer(bob))
        assert video.video_id == "bob000000001"

    def test_list_for_viewer_with_whitelist_still_includes_owned(self, db):
        service, alice, bob, fam, alice_ch, bob_ch = self._setup(db)
        # Alice is restricted to just Family via profile_channels
        viewer = self._viewer(alice, allowed=[fam])
        videos, _ = service.list_for_viewer(viewer)
        ids = {v.video_id for v in videos}
        assert "fam00000001" in ids
        assert "alice0000001" in ids   # own owned is always visible
        assert "bob000000001" not in ids


# ---------------------------------------------------------------------------
# Kid routes
# ---------------------------------------------------------------------------

def _login_kid(client, app, profile_id: int):
    """Set session profile_id directly via the /profiles/select form."""
    resp = client.post("/profiles/select", data={"profile_id": str(profile_id)})
    assert resp.status_code == 302


def _make_kid_in_app(app, name, allowed_channel_ids=None):
    from app.dependencies import get_db
    conn = next(app.dependency_overrides[get_db]())
    pid = _make_kid_profile(conn, name, allowed_channel_ids=allowed_channel_ids)
    return pid


def _make_channel_in_app(app, name, owner_profile_id=None):
    from app.dependencies import get_db
    conn = next(app.dependency_overrides[get_db]())
    return ChannelRepository(conn).create(name, owner_profile_id=owner_profile_id)


class TestKidUploadRoutes:
    def test_get_upload_requires_kid_viewer_anonymous(self, client, app):
        from app.services.auth import AuthService
        AuthService(app.state.config).set_password("tp")
        resp = client.get("/upload")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/profiles"

    def test_get_upload_requires_kid_viewer_parent(self, authed_client, app):
        # authed_client is a parent session. Parents have their own
        # upload page at /parent/upload.
        resp = authed_client.get("/upload")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/profiles"

    def test_get_upload_renders_for_kid(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        _make_channel_in_app(app, "Family")
        _login_kid(client, app, alice)

        resp = client.get("/upload")
        assert resp.status_code == 200
        assert b"Upload a Video" in resp.content
        assert b"Family" in resp.content

    def test_get_upload_dropdown_includes_owned(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        _make_channel_in_app(app, "Family")
        _make_channel_in_app(app, "Alice Art", owner_profile_id=alice)
        _login_kid(client, app, alice)

        resp = client.get("/upload")
        assert resp.status_code == 200
        assert b"Alice Art" in resp.content
        assert b"(yours)" in resp.content

    def test_create_new_channel_as_kid(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        _make_channel_in_app(app, "Family")
        _login_kid(client, app, alice)

        resp = client.post("/upload/new-channel", data={"name": "My Drawings"})
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/upload?selected=")

        # Channel exists with the right owner
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        rows = conn.execute(
            "SELECT id, owner_profile_id FROM channels WHERE name = ?",
            ("My Drawings",),
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["owner_profile_id"] == alice

        # Event logged
        events = conn.execute(
            "SELECT event_type, profile_id, data_json FROM events "
            "WHERE event_type = 'channel_created'"
        ).fetchall()
        assert len(events) == 1
        assert events[0]["profile_id"] == alice
        data = json.loads(events[0]["data_json"])
        assert data["name"] == "My Drawings"

    def test_create_new_channel_rejects_blank_name(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        _login_kid(client, app, alice)
        resp = client.post("/upload/new-channel", data={"name": "   "})
        assert resp.status_code == 400
        assert b"Channel name is required" in resp.content

    def test_create_new_channel_rejects_oversize_name(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        _login_kid(client, app, alice)
        resp = client.post("/upload/new-channel", data={"name": "x" * 100})
        assert resp.status_code == 400

    def test_kid_upload_happy_path(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        family = _make_channel_in_app(app, "Family")
        _login_kid(client, app, alice)

        content = b"k" * 200
        with patch("app.services.uploads.MediaProbeService.validate",
                   return_value=ProbeResult(codec_name="h264", width=320,
                                            height=240, duration_seconds=1.0,
                                            container="mov")):
            with patch("app.services.uploads.ThumbnailService.extract_frame",
                       return_value=None):
                resp = client.post(
                    "/upload",
                    data={"channel_id": str(family), "title": "Hello"},
                    files={"file": ("hello.mp4", content, "video/mp4")},
                )
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/watch/up_")

        # Verify the video row
        import hashlib
        expected_id = f"up_{hashlib.sha256(content).hexdigest()[:16]}"
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        row = conn.execute(
            "SELECT video_id, storage_mode, download_status, channel_id, title "
            "FROM videos WHERE video_id = ?",
            (expected_id,),
        ).fetchone()
        assert row is not None
        assert row["storage_mode"] == "uploaded"
        assert row["download_status"] == "ready"
        assert row["channel_id"] == family
        assert row["title"] == "Hello"

        # Event logged
        events = conn.execute(
            "SELECT profile_id, video_id, data_json FROM events "
            "WHERE event_type = 'video_uploaded_by_kid'"
        ).fetchall()
        assert len(events) == 1
        assert events[0]["profile_id"] == alice
        assert events[0]["video_id"] == expected_id

    def test_kid_upload_refuses_sibling_channel(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        bob = _make_kid_in_app(app, "bob")
        bob_ch = _make_channel_in_app(app, "Bob's Films", owner_profile_id=bob)
        _login_kid(client, app, alice)

        content = b"x" * 50
        resp = client.post(
            "/upload",
            data={"channel_id": str(bob_ch), "title": "Stealing"},
            files={"file": ("sneaky.mp4", content, "video/mp4")},
        )
        assert resp.status_code == 403
        # No videos created
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        count = conn.execute("SELECT COUNT(*) AS cnt FROM videos").fetchone()["cnt"]
        assert count == 0

    def test_kid_upload_refuses_oversize_file(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        family = _make_channel_in_app(app, "Family")
        _login_kid(client, app, alice)

        # Drop the kid ceiling well below the fake file size
        app.state.config.storage.max_kid_upload_bytes = 100
        app.state.config.storage.max_upload_bytes = 100

        content = b"x" * 500
        resp = client.post(
            "/upload",
            data={"channel_id": str(family)},
            files={"file": ("big.mp4", content, "video/mp4")},
        )
        assert resp.status_code == 413
        assert b"too big" in resp.content

    def test_kid_upload_unsupported_codec_shows_kid_friendly_message(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        family = _make_channel_in_app(app, "Family")
        _login_kid(client, app, alice)

        content = b"x" * 60
        hint = "This video uses the 'av1' codec, ffmpeg -i ... -c:v libx264 ..."
        with patch("app.services.uploads.MediaProbeService.validate",
                   side_effect=UnsupportedCodec(hint, "av1", hint)):
            resp = client.post(
                "/upload",
                data={"channel_id": str(family)},
                files={"file": ("av1.mp4", content, "video/mp4")},
            )
        assert resp.status_code == 415
        assert b"Ask a grown-up" in resp.content
        # Critical: the technical ffmpeg hint must NOT leak to the kid
        assert b"libx264" not in resp.content
        assert b"-c:v" not in resp.content

    def test_kid_upload_refuses_when_disk_full(self, client, app):
        alice = _make_kid_in_app(app, "alice")
        family = _make_channel_in_app(app, "Family")
        _login_kid(client, app, alice)

        content = b"x" * 60
        with patch("app.services.storage.StorageService.check_can_write",
                   return_value=(False, "Not enough space")):
            resp = client.post(
                "/upload",
                data={"channel_id": str(family)},
                files={"file": ("clip.mp4", content, "video/mp4")},
            )
        assert resp.status_code == 507
        assert b"grown-up" in resp.content


# ---------------------------------------------------------------------------
# Parent stats friendly labels
# ---------------------------------------------------------------------------

class TestParentStatsPage:
    def test_stats_page_renders_with_aggregated_kpis(self, authed_client, app):
        resp = authed_client.get("/parent/stats")
        assert resp.status_code == 200
        assert b"Videos Completed" in resp.content
        assert b"Top Videos" in resp.content
        assert b"Per Kid" in resp.content


# ---------------------------------------------------------------------------
# Advanced settings max_kid_upload_gb
# ---------------------------------------------------------------------------

class TestSettingsMaxKidUpload:
    def test_max_kid_upload_gb_round_trip(self, authed_client, app):
        resp = authed_client.post("/parent/settings/advanced", data={
            "port": 8080, "host": "0.0.0.0", "default_mode": "cache",
            "min_free_disk_gb": "2.0",
            "max_upload_gb": "10.0",
            "max_kid_upload_gb": "0.75",
            "impersonate": "chrome", "cookies_file": "",
            "cookies_from_browser": "",
            "session_timeout_hours": 24, "log_level": "info",
        })
        assert resp.status_code == 200
        assert app.state.config.storage.max_kid_upload_bytes == int(0.75 * _GB)

    def test_max_kid_upload_gb_negative_rejected(self, authed_client, app):
        original = app.state.config.storage.max_kid_upload_bytes
        resp = authed_client.post("/parent/settings/advanced", data={
            "port": 8080, "host": "0.0.0.0", "default_mode": "cache",
            "min_free_disk_gb": "2.0",
            "max_upload_gb": "10.0",
            "max_kid_upload_gb": "-1",
            "impersonate": "chrome", "cookies_file": "",
            "cookies_from_browser": "",
            "session_timeout_hours": 24, "log_level": "info",
        })
        assert resp.status_code == 200
        assert b"cannot be negative" in resp.content
        assert app.state.config.storage.max_kid_upload_bytes == original
