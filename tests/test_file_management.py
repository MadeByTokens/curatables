"""Tests for Phase 4: web file management.

Covers file-clean video deletion, bulk content operations, channel
delete-with-reassignment, adopt/reassign kid-owned channels, and the
RelocationService preflight + happy path, plus the /parent/settings/move-data
route.
"""

from collections import namedtuple
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest


_Usage = namedtuple("Usage", ["total", "used", "free"])

from app.config import Config
from app.models import Profile, Video
from app.repositories import (
    ChannelRepository, ProfileRepository, VideoRepository,
)
from app.services.channels import ChannelService
from app.services.content import ContentService
from app.services.relocation import RelocationService, RelocationError
from app.services.storage import StorageService
from app.services.thumbnails import ThumbnailService


_GB = 1_073_741_824


def _make_video(video_id, title="Test", channel_id=None, status="active",
                download_status="ready", storage_mode="cache"):
    return Video(
        video_id=video_id, title=title, original_title=title,
        channel_id=channel_id, status=status,
        download_status=download_status, storage_mode=storage_mode,
    )


def _make_content_service(tmp_path, db):
    """Build a ContentService with real storage + thumbnail services."""
    from app.repositories.source_repo import SourceRepository
    storage = StorageService(tmp_path, backend=None, min_free_bytes=0)
    thumbnails = ThumbnailService(tmp_path)
    video_repo = VideoRepository(db)
    channel_repo = ChannelRepository(db)
    src_repo = SourceRepository(db)
    return ContentService(
        video_repo=video_repo, source_repo=src_repo, channel_repo=channel_repo,
        source=None, thumbnails=thumbnails, storage=storage, config=None,
    )


# ---------------------------------------------------------------------------
# File-clean video deletion
# ---------------------------------------------------------------------------

class TestFileCleanDelete:
    def test_deletes_downloaded_video_files(self, tmp_path, db):
        svc = _make_content_service(tmp_path, db)
        vid = "abc12345678"
        svc.video_repo.insert(_make_video(vid, storage_mode="cache"))
        # Create fake video files on disk
        vdir = tmp_path / "videos" / vid
        vdir.mkdir(parents=True)
        (vdir / "video.mp4").write_bytes(b"fake video")
        (vdir / "thumb.jpg").write_bytes(b"fake thumb")

        svc.delete_video(vid)

        assert svc.video_repo.get(vid) is None
        assert not vdir.exists()

    def test_deletes_uploaded_video_files(self, tmp_path, db):
        svc = _make_content_service(tmp_path, db)
        vid = "up_deadbeef12345678"
        svc.video_repo.insert(_make_video(vid, storage_mode="uploaded"))
        # Uploaded files live under uploads/<id>/
        vdir = tmp_path / "uploads" / vid
        vdir.mkdir(parents=True)
        (vdir / "video.mkv").write_bytes(b"fake video")
        # And the thumbnail still goes to videos/<id>/thumb.jpg (Phase 2 quirk)
        thumb_dir = tmp_path / "videos" / vid
        thumb_dir.mkdir(parents=True)
        (thumb_dir / "thumb.jpg").write_bytes(b"fake thumb")

        svc.delete_video(vid)

        assert svc.video_repo.get(vid) is None
        assert not vdir.exists()

    def test_delete_tolerant_of_missing_files(self, tmp_path, db):
        svc = _make_content_service(tmp_path, db)
        vid = "missing0001"
        svc.video_repo.insert(_make_video(vid))
        # No files on disk

        svc.delete_video(vid)  # Should not raise

        assert svc.video_repo.get(vid) is None

    def test_delete_tolerant_of_missing_video_id(self, tmp_path, db):
        svc = _make_content_service(tmp_path, db)
        svc.delete_video("nonexistent")  # Should not raise
        # Still no row
        assert svc.video_repo.get("nonexistent") is None


# ---------------------------------------------------------------------------
# Bulk video operations via POST /parent/content/bulk
# ---------------------------------------------------------------------------

def _seed_videos(app, videos_spec):
    """Create channels and videos in the app's shared DB.

    videos_spec: list of (video_id, title, channel_name, status)
    """
    from app.dependencies import get_db
    conn = next(app.dependency_overrides[get_db]())
    ch_repo = ChannelRepository(conn)
    vid_repo = VideoRepository(conn)
    channel_ids: dict[str, int] = {}
    for vid, title, ch_name, status in videos_spec:
        if ch_name not in channel_ids:
            channel_ids[ch_name] = ch_repo.create(ch_name)
        vid_repo.insert(_make_video(
            vid, title=title, channel_id=channel_ids[ch_name],
            status=status,
        ))
    return channel_ids


def _get_conn(app):
    from app.dependencies import get_db
    return next(app.dependency_overrides[get_db]())


class TestBulkRoute:
    def test_bulk_move(self, authed_client, app):
        channel_ids = _seed_videos(app, [
            ("vidaaaa00001", "First", "Source", "active"),
            ("vidaaaa00002", "Second", "Source", "active"),
            ("vidaaaa00003", "Third", "Source", "active"),
        ])
        dest = ChannelRepository(_get_conn(app)).create("Destination")

        resp = authed_client.post("/parent/content/bulk", data={
            "video_ids": ["vidaaaa00001", "vidaaaa00002"],
            "action": "move",
            "target_channel_id": str(dest),
            "page": "1",
        })
        assert resp.status_code == 302
        assert "flash=" in resp.headers["location"]

        conn = _get_conn(app)
        rows = conn.execute(
            "SELECT video_id, channel_id FROM videos ORDER BY video_id"
        ).fetchall()
        by_id = {r["video_id"]: r["channel_id"] for r in rows}
        assert by_id["vidaaaa00001"] == dest
        assert by_id["vidaaaa00002"] == dest
        assert by_id["vidaaaa00003"] == channel_ids["Source"]

    def test_bulk_delete(self, authed_client, app):
        _seed_videos(app, [
            ("viddel000001", "One", "C", "active"),
            ("viddel000002", "Two", "C", "active"),
            ("viddel000003", "Three", "C", "active"),
        ])

        resp = authed_client.post("/parent/content/bulk", data={
            "video_ids": ["viddel000001", "viddel000003"],
            "action": "delete",
            "page": "1",
        })
        assert resp.status_code == 302

        conn = _get_conn(app)
        remaining = {r["video_id"] for r in conn.execute("SELECT video_id FROM videos").fetchall()}
        assert remaining == {"viddel000002"}

    def test_bulk_hide_and_unhide(self, authed_client, app):
        _seed_videos(app, [
            ("vidhide00001", "One", "C", "active"),
            ("vidhide00002", "Two", "C", "active"),
        ])

        resp = authed_client.post("/parent/content/bulk", data={
            "video_ids": ["vidhide00001", "vidhide00002"],
            "action": "hide",
            "page": "1",
        })
        assert resp.status_code == 302
        conn = _get_conn(app)
        rows = conn.execute("SELECT status FROM videos").fetchall()
        assert all(r["status"] == "hidden" for r in rows)

        resp = authed_client.post("/parent/content/bulk", data={
            "video_ids": ["vidhide00001"],
            "action": "unhide",
            "page": "1",
        })
        assert resp.status_code == 302
        conn = _get_conn(app)
        statuses = {r["video_id"]: r["status"] for r in conn.execute(
            "SELECT video_id, status FROM videos"
        ).fetchall()}
        assert statuses["vidhide00001"] == "active"
        assert statuses["vidhide00002"] == "hidden"

    def test_bulk_refuses_empty_selection(self, authed_client, app):
        resp = authed_client.post("/parent/content/bulk", data={
            "action": "delete",
            "page": "1",
        })
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "flash_type=error" in loc
        assert "at+least+one" in loc.lower() or "at%20least%20one" in loc.lower()

    def test_bulk_refuses_unknown_action(self, authed_client, app):
        _seed_videos(app, [("vidunk000001", "x", "C", "active")])
        resp = authed_client.post("/parent/content/bulk", data={
            "video_ids": ["vidunk000001"],
            "action": "whatever",
            "page": "1",
        })
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "flash_type=error" in loc

    def test_bulk_move_rejects_invalid_target(self, authed_client, app):
        _seed_videos(app, [("vidbad0000001", "x", "C", "active")])
        resp = authed_client.post("/parent/content/bulk", data={
            "video_ids": ["vidbad0000001"],
            "action": "move",
            "target_channel_id": "99999",
            "page": "1",
        })
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "flash_type=error" in loc

    def test_bulk_move_rejects_missing_target(self, authed_client, app):
        _seed_videos(app, [("vidnopick0001", "x", "C", "active")])
        resp = authed_client.post("/parent/content/bulk", data={
            "video_ids": ["vidnopick0001"],
            "action": "move",
            "target_channel_id": "",
            "page": "1",
        })
        assert resp.status_code == 302
        loc = resp.headers["location"]
        assert "flash_type=error" in loc


# ---------------------------------------------------------------------------
# Channel delete with reassignment
# ---------------------------------------------------------------------------

class TestChannelDeleteReassign:
    def test_delete_with_reassignment_moves_videos(self, db):
        ch_repo = ChannelRepository(db)
        vid_repo = VideoRepository(db)
        source = ch_repo.create("Source")
        dest = ch_repo.create("Destination")
        vid_repo.insert(_make_video("vidmoved0001", channel_id=source))
        vid_repo.insert(_make_video("vidmoved0002", channel_id=source))
        # A video in the dest that should be untouched
        vid_repo.insert(_make_video("videstay0001", channel_id=dest))

        ch_repo.delete(source, reassign_to=dest)

        assert ch_repo.get(source) is None
        rows = db.execute(
            "SELECT video_id, channel_id FROM videos ORDER BY video_id"
        ).fetchall()
        by_id = {r["video_id"]: r["channel_id"] for r in rows}
        assert by_id["vidmoved0001"] == dest
        assert by_id["vidmoved0002"] == dest
        assert by_id["videstay0001"] == dest

    def test_delete_without_reassignment_orphans_videos(self, db):
        """Regression: existing behavior must still work."""
        ch_repo = ChannelRepository(db)
        vid_repo = VideoRepository(db)
        source = ch_repo.create("Source")
        vid_repo.insert(_make_video("vidorp0000001", channel_id=source))

        ch_repo.delete(source)

        rows = db.execute(
            "SELECT video_id, channel_id FROM videos WHERE video_id = ?",
            ("vidorp0000001",),
        ).fetchone()
        assert rows["channel_id"] is None

    def test_route_delete_self_reassign_falls_back_to_orphan(self, authed_client, app):
        ch_repo = ChannelRepository(_get_conn(app))
        cid = ch_repo.create("Solo")
        VideoRepository(_get_conn(app)).insert(
            _make_video("vidsolo000001", channel_id=cid))

        resp = authed_client.post(
            f"/parent/channels/{cid}/delete",
            data={"reassign_to": str(cid)},
        )
        assert resp.status_code == 302

        conn = _get_conn(app)
        row = conn.execute(
            "SELECT channel_id FROM videos WHERE video_id = ?",
            ("vidsolo000001",),
        ).fetchone()
        assert row["channel_id"] is None


# ---------------------------------------------------------------------------
# Adopt / reassign kid-owned channels
# ---------------------------------------------------------------------------

class TestAdoptKidChannel:
    def _make_profile(self, db, name):
        repo = ProfileRepository(db)
        return repo.create(Profile(name=name, display_name=name.title(), pin=""))

    def test_adopt_kid_channel_sets_owner_null(self, authed_client, app):
        conn = _get_conn(app)
        alice = self._make_profile(conn, "alice")
        cid = ChannelRepository(conn).create("Alice Art", owner_profile_id=alice)

        resp = authed_client.post(f"/parent/channels/{cid}/edit", data={
            "name": "Alice Art",
            "description": "",
            "position": "0",
            "owner_profile_id": "",
        })
        assert resp.status_code == 302

        conn = _get_conn(app)
        ch = ChannelRepository(conn).get(cid)
        assert ch is not None
        assert ch.owner_profile_id is None

    def test_reassign_kid_channel_to_other_kid(self, authed_client, app):
        conn = _get_conn(app)
        alice = self._make_profile(conn, "alice")
        bob = self._make_profile(conn, "bob")
        cid = ChannelRepository(conn).create("Shared Art", owner_profile_id=alice)

        resp = authed_client.post(f"/parent/channels/{cid}/edit", data={
            "name": "Shared Art",
            "description": "",
            "position": "0",
            "owner_profile_id": str(bob),
        })
        assert resp.status_code == 302

        conn = _get_conn(app)
        ch = ChannelRepository(conn).get(cid)
        assert ch is not None
        assert ch.owner_profile_id == bob


# ---------------------------------------------------------------------------
# RelocationService preflight + happy path
# ---------------------------------------------------------------------------

class TestRelocationService:
    def _make_config(self, data_dir: Path) -> Config:
        c = Config()
        c.storage.path = str(data_dir)
        return c

    def _make_service(self, db, data_dir: Path) -> RelocationService:
        config = self._make_config(data_dir)
        repo = VideoRepository(db)
        return RelocationService(config, repo)

    def test_refuses_same_path(self, db, tmp_path):
        svc = self._make_service(db, tmp_path)
        with pytest.raises(RelocationError, match="same"):
            svc.move(str(tmp_path))

    def test_refuses_empty_path(self, db, tmp_path):
        svc = self._make_service(db, tmp_path)
        with pytest.raises(RelocationError, match="required"):
            svc.move("")

    def test_refuses_relative_path(self, db, tmp_path):
        svc = self._make_service(db, tmp_path)
        with pytest.raises(RelocationError, match="absolute"):
            svc.move("relative/path")

    def test_refuses_non_empty_target(self, db, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        target = tmp_path / "dest"
        target.mkdir()
        (target / "existing.txt").write_text("leave me alone")
        svc = self._make_service(db, source)
        with pytest.raises(RelocationError, match="not empty"):
            svc.move(str(target))

    def test_refuses_nonexistent_parent(self, db, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        svc = self._make_service(db, source)
        # Use a deeply nested path whose parent doesn't exist
        missing = tmp_path / "missing" / "nested" / "data"
        with pytest.raises(RelocationError, match="does not exist"):
            svc.move(str(missing))

    def test_refuses_in_flight_downloads(self, db, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        target = tmp_path / "dest"
        # Insert a downloading video
        ch = ChannelRepository(db).create("X")
        VideoRepository(db).insert(_make_video(
            "viddnl000001", channel_id=ch, download_status="downloading"))
        svc = self._make_service(db, source)
        with pytest.raises(RelocationError, match="downloading"):
            svc.move(str(target))

    def test_refuses_insufficient_space(self, db, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        target = tmp_path / "dest"
        ch = ChannelRepository(db).create("X")
        VideoRepository(db).insert(_make_video(
            "vidbig0000001", channel_id=ch, status="active", download_status="ready"))
        VideoRepository(db).update("vidbig0000001", file_size=5 * _GB)

        svc = self._make_service(db, source)
        # Patch disk_usage to return almost nothing free
        with patch("app.services.relocation.shutil.disk_usage",
                   return_value=_Usage(_GB, _GB - 1, 1)):
            with pytest.raises(RelocationError, match="free"):
                svc.move(str(target))

    def test_happy_path_moves_files_and_saves_config(self, db, tmp_path):
        source = tmp_path / "src"
        source.mkdir()
        (source / "db").mkdir()
        (source / "db" / "curatables.db").write_bytes(b"fake db")
        (source / "videos").mkdir()
        (source / "videos" / "abc").mkdir()
        (source / "videos" / "abc" / "video.mp4").write_bytes(b"fake mp4")

        target = tmp_path / "dest"

        svc = self._make_service(db, source)
        # Patch disk_usage to always report plenty of room
        with patch("app.services.relocation.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 10 * _GB, 90 * _GB)):
            result = svc.move(str(target))

        assert result == target
        assert not source.exists()
        assert target.exists()
        assert (target / "db" / "curatables.db").exists()
        assert (target / "videos" / "abc" / "video.mp4").exists()
        assert (target / "config.json").exists()
        assert svc.config.storage.path == str(target)


# ---------------------------------------------------------------------------
# Relocation route
# ---------------------------------------------------------------------------

class TestRelocationRoute:
    def test_route_happy_path(self, authed_client, app, tmp_path):
        target = tmp_path / "new-data-dir"

        with patch("app.services.relocation.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 10 * _GB, 90 * _GB)):
            with patch("app.services.relocation.shutil.move") as mock_move:
                with patch.object(Config, "save") as mock_save:
                    resp = authed_client.post(
                        "/parent/settings/move-data",
                        data={"new_data_dir": str(target)},
                    )
        assert resp.status_code == 200
        assert b"moved to" in resp.content.lower() or b"Data directory moved" in resp.content
        mock_move.assert_called_once()
        mock_save.assert_called_once()
        assert app.state.config.storage.path == str(target)

    def test_route_refuses_in_flight_downloads(self, authed_client, app, tmp_path):
        conn = _get_conn(app)
        ChannelRepository(conn).create("X")
        VideoRepository(conn).insert(_make_video(
            "vidroute00001",
            channel_id=ChannelRepository(conn).get(1).id,
            download_status="downloading",
        ))
        target = tmp_path / "new-data-dir"

        resp = authed_client.post(
            "/parent/settings/move-data",
            data={"new_data_dir": str(target)},
        )
        assert resp.status_code == 200
        assert b"downloading" in resp.content.lower()

    def test_route_refuses_relative_path(self, authed_client, app):
        resp = authed_client.post(
            "/parent/settings/move-data",
            data={"new_data_dir": "relative/path"},
        )
        assert resp.status_code == 200
        assert b"absolute" in resp.content.lower()
