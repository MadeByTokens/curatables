"""Tests for kid library personalization: per-kid video title/thumbnail
overrides, personal tags + tag cloud, and per-kid channel bookmarks."""

import io
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.db.connection import create_connection
from app.db.schema import init_schema
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.repositories.override_repo import ProfileVideoOverrideRepository
from app.repositories.tag_repo import TagRepository
from app.repositories.profile_channel_video_repo import ProfileChannelVideoRepository
from app.services.kid_library import KidLibraryService


def _mk_kid(db, name, allowed_channel_ids=None):
    return ProfileRepository(db).create(Profile(
        name=name, display_name=name.title(), pin="",
        allowed_channel_ids=allowed_channel_ids or [],
    ))


def _mk_video(db, video_id="v1", title="Original", channel_id=None):
    VideoRepository(db).insert(Video(
        video_id=video_id, title=title, original_title=title,
        channel_id=channel_id, status="active", download_status="ready",
    ))
    return video_id


def _mk_service(db, tmp_path):
    return KidLibraryService(
        ProfileVideoOverrideRepository(db),
        TagRepository(db),
        ProfileChannelVideoRepository(db),
        Path(tmp_path),
    )


# ---------------------------------------------------------------------------
# Schema sanity
# ---------------------------------------------------------------------------

class TestSchema:
    def test_fresh_db_has_new_tables(self, db):
        names = {r["name"] for r in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        assert "profile_video_overrides" in names
        assert "tags" in names
        assert "profile_video_tags" in names
        assert "profile_channel_videos" in names

    def test_channels_has_new_columns(self, db):
        cols = {c["name"] for c in db.execute(
            "PRAGMA table_info(channels)").fetchall()}
        assert "banner_filename" in cols
        assert "icon_filename" in cols
        assert "color" in cols


# ---------------------------------------------------------------------------
# Title overrides
# ---------------------------------------------------------------------------

class TestTitleOverrides:
    def test_set_and_apply_title_override(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1", "Original Title")
        svc = _mk_service(db, tmp_path)
        svc.set_title(alice, "v1", "Alice's Title")

        video = VideoRepository(db).get("v1")
        assert video.title == "Original Title"  # canonical untouched
        [patched] = svc.apply_overrides(alice, [video])
        assert patched.title == "Alice's Title"  # patched for this kid

    def test_empty_title_clears_override(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1", "Original Title")
        svc = _mk_service(db, tmp_path)
        svc.set_title(alice, "v1", "Alice's Title")
        svc.set_title(alice, "v1", "")  # clear

        video = VideoRepository(db).get("v1")
        [patched] = svc.apply_overrides(alice, [video])
        assert patched.title == "Original Title"

    def test_apply_overrides_does_not_mutate_input(self, db, tmp_path):
        """Regression guard: apply_overrides must return patched copies,
        not mutate the input Video objects. Any future cache layer in
        front of the video repo would silently poison shared instances
        if this invariant broke."""
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1", "Original Title")
        svc = _mk_service(db, tmp_path)
        svc.set_title(alice, "v1", "Alice's Title")

        video = VideoRepository(db).get("v1")
        original_title = video.title
        original_description = video.description

        out = svc.apply_overrides(alice, [video])

        # Input untouched
        assert video.title == original_title
        assert video.description == original_description
        # Returned copy carries the override
        assert out[0].title == "Alice's Title"
        # And it's a different object from the input
        assert out[0] is not video

    def test_override_is_per_kid(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        bob = _mk_kid(db, "bob")
        _mk_video(db, "v1", "Original Title")
        svc = _mk_service(db, tmp_path)
        svc.set_title(alice, "v1", "Alice's Title")

        # Alice sees her override
        v_alice = VideoRepository(db).get("v1")
        [v_alice_patched] = svc.apply_overrides(alice, [v_alice])
        assert v_alice_patched.title == "Alice's Title"

        # Bob sees the canonical title
        v_bob = VideoRepository(db).get("v1")
        [v_bob_patched] = svc.apply_overrides(bob, [v_bob])
        assert v_bob_patched.title == "Original Title"


class TestDescriptionOverrides:
    def test_set_and_apply_description_override(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        VideoRepository(db).insert(Video(
            video_id="v1", title="T", original_title="T",
            description="Original description",
            status="active", download_status="ready",
        ))
        svc = _mk_service(db, tmp_path)
        svc.set_description(alice, "v1", "Alice's description")

        video = VideoRepository(db).get("v1")
        assert video.description == "Original description"
        [patched] = svc.apply_overrides(alice, [video])
        assert patched.description == "Alice's description"

    def test_empty_description_clears_override(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        VideoRepository(db).insert(Video(
            video_id="v1", title="T", original_title="T",
            description="Original",
            status="active", download_status="ready",
        ))
        svc = _mk_service(db, tmp_path)
        svc.set_description(alice, "v1", "Alice's")
        svc.set_description(alice, "v1", "")

        video = VideoRepository(db).get("v1")
        [patched] = svc.apply_overrides(alice, [video])
        assert patched.description == "Original"


class TestIndividualReset:
    def test_clear_title_keeps_description(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1", "Orig")
        svc = _mk_service(db, tmp_path)
        svc.set_title(alice, "v1", "custom title")
        svc.set_description(alice, "v1", "custom desc")

        svc.clear_title(alice, "v1")

        ov = svc.get_overrides(alice, "v1")
        assert ov["title"] is None
        assert ov["description"] == "custom desc"

    def test_clear_description_keeps_title(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1", "Orig")
        svc = _mk_service(db, tmp_path)
        svc.set_title(alice, "v1", "custom title")
        svc.set_description(alice, "v1", "custom desc")

        svc.clear_description(alice, "v1")

        ov = svc.get_overrides(alice, "v1")
        assert ov["title"] == "custom title"
        assert ov["description"] is None

    def test_clear_thumbnail_keeps_title_and_description(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1", "Orig")
        svc = _mk_service(db, tmp_path)
        svc.set_title(alice, "v1", "custom")
        svc.set_description(alice, "v1", "desc")
        svc.upload_thumbnail(alice, "v1", b"xx", "a.jpg")

        svc.clear_thumbnail(alice, "v1")

        ov = svc.get_overrides(alice, "v1")
        assert ov["title"] == "custom"
        assert ov["description"] == "desc"
        assert ov["has_custom_thumb"] == 0
        assert svc.get_custom_thumb_path(alice, "v1") is None


# ---------------------------------------------------------------------------
# Thumbnail overrides
# ---------------------------------------------------------------------------

class TestThumbnailOverrides:
    def test_upload_and_get_custom_thumb_path(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.upload_thumbnail(alice, "v1", b"\xff\xd8\xffxxFAKEJPEG", "pic.jpg")

        path = svc.get_custom_thumb_path(alice, "v1")
        assert path is not None
        assert path.exists()
        assert path.suffix == ".jpg"
        assert svc.has_custom_thumb(alice, "v1") is True

    def test_clear_thumbnail_removes_file_and_flag(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.upload_thumbnail(alice, "v1", b"xxx", "pic.png")
        assert svc.has_custom_thumb(alice, "v1") is True

        svc.clear_thumbnail(alice, "v1")
        assert svc.has_custom_thumb(alice, "v1") is False
        assert svc.get_custom_thumb_path(alice, "v1") is None

    def test_upload_replaces_previous_file(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.upload_thumbnail(alice, "v1", b"first", "a.jpg")
        svc.upload_thumbnail(alice, "v1", b"second", "b.png")

        # Only one file remains, with the new extension
        thumb_dir = tmp_path / "thumbnails" / "profiles" / str(alice)
        files = list(thumb_dir.glob("v1.*"))
        assert len(files) == 1
        assert files[0].suffix == ".png"
        assert files[0].read_bytes() == b"second"


# ---------------------------------------------------------------------------
# Tags
# ---------------------------------------------------------------------------

class TestTags:
    def test_add_and_list_tags(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.add_tag(alice, "v1", "science")
        svc.add_tag(alice, "v1", "funny")

        names = [t.name for t in svc.tags_for_video(alice, "v1")]
        assert sorted(names) == ["funny", "science"]

    def test_tags_are_case_insensitive(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.add_tag(alice, "v1", "Science")
        svc.add_tag(alice, "v1", "science")

        # Same tag, no duplicate
        tag_count = db.execute("SELECT COUNT(*) AS n FROM tags").fetchone()["n"]
        assert tag_count == 1
        junction = db.execute(
            "SELECT COUNT(*) AS n FROM profile_video_tags "
            "WHERE profile_id = ? AND video_id = ?",
            (alice, "v1"),
        ).fetchone()["n"]
        assert junction == 1

    def test_remove_tag(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.add_tag(alice, "v1", "x")
        tag = svc.tags_for_video(alice, "v1")[0]
        svc.remove_tag(alice, "v1", tag.id)
        assert svc.tags_for_video(alice, "v1") == []

    def test_sync_tags_replaces_all(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.add_tag(alice, "v1", "old1")
        svc.add_tag(alice, "v1", "old2")
        svc.sync_tags(alice, "v1", ["new1", "new2", "new3"])
        names = sorted(t.name for t in svc.tags_for_video(alice, "v1"))
        assert names == ["new1", "new2", "new3"]

    def test_tags_are_per_kid(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        bob = _mk_kid(db, "bob")
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.add_tag(alice, "v1", "alice-only")
        assert svc.tags_for_video(alice, "v1") != []
        assert svc.tags_for_video(bob, "v1") == []

    def test_tag_cloud_counts(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        cid = ChannelRepository(db).create("Family")
        _mk_video(db, "v1", channel_id=cid)
        _mk_video(db, "v2", channel_id=cid)
        _mk_video(db, "v3", channel_id=cid)
        svc = _mk_service(db, tmp_path)
        svc.add_tag(alice, "v1", "science")
        svc.add_tag(alice, "v2", "science")
        svc.add_tag(alice, "v3", "science")
        svc.add_tag(alice, "v1", "funny")

        cloud = svc.tag_cloud(alice, [cid])
        by_name = {c["name"]: c["count"] for c in cloud}
        assert by_name == {"science": 3, "funny": 1}

    def test_videos_by_tag_respects_visibility(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        c1 = ChannelRepository(db).create("A")
        c2 = ChannelRepository(db).create("B")
        _mk_video(db, "v1", channel_id=c1)
        _mk_video(db, "v2", channel_id=c2)
        svc = _mk_service(db, tmp_path)
        svc.add_tag(alice, "v1", "t")
        svc.add_tag(alice, "v2", "t")

        # Limited to c1 only — v2 should be filtered out
        ids, total = svc.videos_by_tag(alice, "t", [c1])
        assert ids == ["v1"]
        assert total == 1


# ---------------------------------------------------------------------------
# Channel bookmarks
# ---------------------------------------------------------------------------

class TestChannelBookmarks:
    def test_bookmark_and_list(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        kid_channel = ChannelRepository(db).create(
            "Alice's Picks", owner_profile_id=alice)
        parent_channel = ChannelRepository(db).create("Family")
        _mk_video(db, "v1", channel_id=parent_channel)
        svc = _mk_service(db, tmp_path)
        svc.bookmark_video(alice, kid_channel, "v1")

        ids, total = svc.channel_video_ids(alice, kid_channel)
        assert ids == ["v1"]
        assert total == 1

    def test_bookmark_does_not_move_video(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        kid_channel = ChannelRepository(db).create(
            "Alice's Picks", owner_profile_id=alice)
        parent_channel = ChannelRepository(db).create("Family")
        _mk_video(db, "v1", channel_id=parent_channel)
        svc = _mk_service(db, tmp_path)
        svc.bookmark_video(alice, kid_channel, "v1")

        # Canonical channel_id is unchanged
        video = VideoRepository(db).get("v1")
        assert video.channel_id == parent_channel

    def test_unbookmark(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        kid_channel = ChannelRepository(db).create(
            "Alice's Picks", owner_profile_id=alice)
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.bookmark_video(alice, kid_channel, "v1")
        svc.unbookmark_video(alice, kid_channel, "v1")

        ids, total = svc.channel_video_ids(alice, kid_channel)
        assert ids == []
        assert total == 0

    def test_same_video_in_multiple_kid_channels(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        ch1 = ChannelRepository(db).create("A", owner_profile_id=alice)
        ch2 = ChannelRepository(db).create("B", owner_profile_id=alice)
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.bookmark_video(alice, ch1, "v1")
        svc.bookmark_video(alice, ch2, "v1")

        assert svc.channels_for_video(alice, "v1") == [ch1, ch2] or \
               svc.channels_for_video(alice, "v1") == [ch2, ch1]

    def test_bookmarks_are_per_kid(self, db, tmp_path):
        alice = _mk_kid(db, "alice")
        bob = _mk_kid(db, "bob")
        alice_ch = ChannelRepository(db).create("A", owner_profile_id=alice)
        _mk_video(db, "v1")
        svc = _mk_service(db, tmp_path)
        svc.bookmark_video(alice, alice_ch, "v1")
        # Bob sees nothing
        assert svc.channel_video_ids(bob, alice_ch) == ([], 0)


# ---------------------------------------------------------------------------
# Channel art columns
# ---------------------------------------------------------------------------

class TestChannelArt:
    def test_channel_art_fields_default_and_update(self, db):
        repo = ChannelRepository(db)
        cid = repo.create("My Channel")
        ch = repo.get(cid)
        assert ch.banner_filename == ""
        assert ch.icon_filename == ""
        assert ch.color == "#2a9d8f"

        repo.update(cid, banner_filename="banner.jpg",
                    icon_filename="icon.png", color="#ff0000")
        ch = repo.get(cid)
        assert ch.banner_filename == "banner.jpg"
        assert ch.icon_filename == "icon.png"
        assert ch.color == "#ff0000"


# ---------------------------------------------------------------------------
# End-to-end route access control
# ---------------------------------------------------------------------------

class TestRouteAccess:
    def test_parent_cannot_access_kid_video_edit(self, authed_client):
        # Parents are redirected by require_child to /profiles
        resp = authed_client.get("/video/v1/edit")
        # require_child raises NotAChild → global handler redirects
        assert resp.status_code in (302, 303, 307)

    def test_anonymous_cannot_access_tags(self, client):
        resp = client.get("/tags")
        # Anonymous viewer redirected to home (which redirects to /profiles)
        assert resp.status_code in (302, 303, 307)

# ---------------------------------------------------------------------------
# Concurrency — multi-tab race windows in upsert-style writes
# ---------------------------------------------------------------------------

def _file_backed_db(tmp_path):
    """WAL concurrency requires a real file, not :memory:. Each thread
    opens its own connection to the same file."""
    db_path = tmp_path / "concur.db"
    conn = create_connection(db_path)
    init_schema(conn)
    conn.close()
    return db_path


class TestConcurrency:
    def test_tag_get_or_create_under_threads(self, tmp_path):
        db_path = _file_backed_db(tmp_path)
        barrier = threading.Barrier(50)
        results = []
        errors = []

        def worker():
            barrier.wait()
            try:
                conn = create_connection(db_path)
                tid = TagRepository(conn).get_or_create("science")
                results.append(tid)
                conn.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent get_or_create raised: {errors}"
        assert len(results) == 50
        assert len(set(results)) == 1  # all threads got the same tag id

        conn = create_connection(db_path)
        n = conn.execute("SELECT COUNT(*) AS n FROM tags").fetchone()["n"]
        conn.close()
        assert n == 1

    def test_override_upsert_under_threads(self, tmp_path):
        db_path = _file_backed_db(tmp_path)
        # Seed a profile and a video so FK constraints pass
        conn = create_connection(db_path)
        alice = ProfileRepository(conn).create(Profile(
            name="alice", display_name="Alice", pin="",
            allowed_channel_ids=[],
        ))
        VideoRepository(conn).insert(Video(
            video_id="raceV1", title="orig", original_title="orig",
            status="active", download_status="ready",
        ))
        conn.close()

        barrier = threading.Barrier(20)
        errors = []

        def worker(i):
            barrier.wait()
            try:
                conn = create_connection(db_path)
                ProfileVideoOverrideRepository(conn).upsert(
                    alice, "raceV1", title=f"title-{i}")
                conn.close()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,))
                   for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == [], f"Concurrent upsert raised: {errors}"
        conn = create_connection(db_path)
        rows = conn.execute(
            "SELECT title FROM profile_video_overrides "
            "WHERE profile_id = ? AND video_id = ?",
            (alice, "raceV1"),
        ).fetchall()
        conn.close()
        assert len(rows) == 1  # exactly one row, no duplicate-PK errors
        assert rows[0]["title"].startswith("title-")  # some thread's value won


    def test_media_thumb_has_no_cache_header(self, app, authed_client,
                                             tmp_path):
        """Thumbnail responses must set Cache-Control: no-cache so browsers
        revalidate after a custom thumbnail upload."""
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        cid = ChannelRepository(conn).create("Family")
        VideoRepository(conn).insert(Video(
            video_id="vidchtest01", title="T", original_title="T",
            channel_id=cid, status="active", download_status="ready",
        ))
        # Seed a canonical thumbnail file so the route doesn't fall
        # through to the placeholder SVG (which caches differently).
        video_dir = Path(app.state.config.data_dir) / "videos" / "vidchtest01"
        video_dir.mkdir(parents=True, exist_ok=True)
        (video_dir / "thumb.jpg").write_bytes(b"\xff\xd8\xffxx")

        resp = authed_client.get("/media/thumb/vidchtest01")
        assert resp.status_code == 200
        cc = resp.headers.get("cache-control", "")
        assert "no-cache" in cc
