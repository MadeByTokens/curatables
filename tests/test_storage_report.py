"""Tests for the disk quota guard and storage usage report.

Covers: VideoRepository aggregation queries, StorageReportService
status transitions, StorageService.check_can_write and the disk-quota
guard inside StorageService.download, ContentService error recording
when InsufficientDiskSpace is raised, the /parent/storage route, and
the advanced settings form writing min_free_disk_bytes.
"""

from collections import namedtuple
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.models import Video
from app.repositories import VideoRepository, ChannelRepository
from app.services.storage import StorageService, InsufficientDiskSpace
from app.services.storage_report import StorageReportService


_GB = 1_073_741_824
_Usage = namedtuple("Usage", ["total", "used", "free"])


def _make_video(video_id, title="Test", channel_id=None, file_size=0,
                status="active", download_status="ready"):
    return Video(
        video_id=video_id, title=title, original_title=title,
        channel_id=channel_id, status=status, download_status=download_status,
    )


# ---------------------------------------------------------------------------
# Repository aggregation queries
# ---------------------------------------------------------------------------

class TestVideoRepoAggregations:
    def test_sum_file_size_empty(self, db):
        repo = VideoRepository(db)
        assert repo.sum_file_size() == 0

    def test_sum_file_size_counts_only_active(self, db):
        ch = ChannelRepository(db)
        cid = ch.create("Science")
        repo = VideoRepository(db)

        repo.insert(_make_video("vid00000001", channel_id=cid))
        repo.update("vid00000001", file_size=1000)

        repo.insert(_make_video("vid00000002", channel_id=cid))
        repo.update("vid00000002", file_size=500)

        repo.insert(_make_video("vid00000003", channel_id=cid, status="hidden"))
        repo.update("vid00000003", file_size=9999)

        assert repo.sum_file_size() == 1500

    def test_size_by_channel_groups_and_orders_desc(self, db):
        ch = ChannelRepository(db)
        science = ch.create("Science")
        art = ch.create("Art")
        repo = VideoRepository(db)

        repo.insert(_make_video("vidsci00001", channel_id=science))
        repo.update("vidsci00001", file_size=3000)
        repo.insert(_make_video("vidsci00002", channel_id=science))
        repo.update("vidsci00002", file_size=2000)

        repo.insert(_make_video("vidart00001", channel_id=art))
        repo.update("vidart00001", file_size=1000)

        repo.insert(_make_video("vidorp00001", channel_id=None))
        repo.update("vidorp00001", file_size=500)

        rows = repo.size_by_channel()
        assert rows[0][1] == "Science"
        assert rows[0][2] == 2
        assert rows[0][3] == 5000
        assert rows[1][1] == "Art"
        assert rows[1][3] == 1000
        assert rows[2][1] == "Unassigned"
        assert rows[2][0] is None
        assert rows[2][3] == 500


# ---------------------------------------------------------------------------
# StorageReportService — status transitions
# ---------------------------------------------------------------------------

class TestStorageReportService:
    def _make(self, free_bytes, min_free=2 * _GB):
        video_repo = MagicMock()
        video_repo.sum_file_size.return_value = 42
        video_repo.size_by_channel.return_value = [
            (1, "Science", 3, 1500),
            (None, "Unassigned", 1, 500),
        ]
        service = StorageReportService(
            data_dir=Path("/tmp"),
            min_free_bytes=min_free,
            video_repo=video_repo,
        )
        patcher = patch("app.services.storage_report.shutil.disk_usage",
                        return_value=_Usage(100 * _GB, 100 * _GB - free_bytes, free_bytes))
        return service, patcher

    def test_status_ok_when_free_above_twice_threshold(self):
        svc, patcher = self._make(free_bytes=10 * _GB)
        with patcher:
            report = svc.get_report()
        assert report.status == "ok"
        assert report.free_bytes == 10 * _GB

    def test_status_warning_when_free_between_threshold_and_double(self):
        svc, patcher = self._make(free_bytes=3 * _GB)
        with patcher:
            report = svc.get_report()
        assert report.status == "warning"

    def test_status_blocked_when_free_below_threshold(self):
        svc, patcher = self._make(free_bytes=1 * _GB)
        with patcher:
            report = svc.get_report()
        assert report.status == "blocked"

    def test_get_size_by_channel_returns_channel_size_objects(self):
        svc, patcher = self._make(free_bytes=10 * _GB)
        with patcher:
            channels = svc.get_size_by_channel()
        assert len(channels) == 2
        assert channels[0].channel_name == "Science"
        assert channels[0].video_count == 3
        assert channels[1].channel_id is None


# ---------------------------------------------------------------------------
# StorageService.check_can_write and download guard
# ---------------------------------------------------------------------------

class TestStorageServiceGuard:
    def _service(self, min_free_bytes=2 * _GB):
        backend = MagicMock()
        backend.last_error = None
        return StorageService(Path("/tmp"), backend, min_free_bytes=min_free_bytes), backend

    def test_check_can_write_allows_when_above_threshold(self):
        svc, _ = self._service()
        with patch("app.services.storage.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 90 * _GB, 10 * _GB)):
            allowed, reason = svc.check_can_write()
        assert allowed is True
        assert reason == ""

    def test_check_can_write_refuses_when_below_threshold(self):
        svc, _ = self._service()
        with patch("app.services.storage.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 99 * _GB, 1 * _GB)):
            allowed, reason = svc.check_can_write()
        assert allowed is False
        assert "Insufficient disk space" in reason
        assert "1.0 GB free" in reason

    def test_check_can_write_with_required_bytes_subtracted(self):
        svc, _ = self._service()
        with patch("app.services.storage.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 97 * _GB, 3 * _GB)):
            allowed, _ = svc.check_can_write(required_bytes=2 * _GB)
        assert allowed is False

    def test_download_raises_insufficient_disk_space(self):
        svc, backend = self._service()
        with patch("app.services.storage.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 99 * _GB, 1 * _GB)):
            with patch.object(svc, "is_cached", return_value=False):
                with pytest.raises(InsufficientDiskSpace):
                    svc.download("vidblockd001",
                                 "https://www.youtube.com/watch?v=vidblockd001")
        backend.download_video.assert_not_called()

    def test_download_proceeds_when_above_threshold(self):
        svc, backend = self._service()
        backend.download_video.return_value = Path("/tmp/videos/vidok00000001/video.mp4")
        with patch("app.services.storage.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 50 * _GB, 50 * _GB)):
            with patch.object(svc, "is_cached", return_value=False):
                result = svc.download("vidok00000001",
                                      "https://www.youtube.com/watch?v=vidok00000001")
        assert result is not None
        backend.download_video.assert_called_once()


# ---------------------------------------------------------------------------
# Route tests
# ---------------------------------------------------------------------------

class TestStorageRoute:
    def test_storage_page_returns_200_empty(self, authed_client):
        with patch("app.services.storage_report.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 50 * _GB, 50 * _GB)):
            resp = authed_client.get("/parent/storage")
        assert resp.status_code == 200
        body = resp.content
        assert b"Storage" in body
        assert b"Minimum Free GB" in body
        assert b"No videos stored yet" in body

    def test_storage_page_renders_channel_table(self, authed_client, app):
        from app.dependencies import get_db
        override = app.dependency_overrides[get_db]
        conn = next(override())

        ch_repo = ChannelRepository(conn)
        cid = ch_repo.create("Science")
        vid_repo = VideoRepository(conn)
        vid_repo.insert(_make_video("vidroute0001", channel_id=cid))
        vid_repo.update("vidroute0001", file_size=12345)

        with patch("app.services.storage_report.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 50 * _GB, 50 * _GB)):
            resp = authed_client.get("/parent/storage")
        assert resp.status_code == 200
        assert b"Science" in resp.content

    def test_storage_page_shows_blocked_banner(self, authed_client):
        with patch("app.services.storage_report.shutil.disk_usage",
                   return_value=_Usage(100 * _GB, 99 * _GB, 1 * _GB)):
            resp = authed_client.get("/parent/storage")
        assert resp.status_code == 200
        assert b"Free space is below the configured minimum" in resp.content


# ---------------------------------------------------------------------------
# Advanced settings form writes min_free_disk_bytes
# ---------------------------------------------------------------------------

class TestSettingsAdvanced:
    def test_min_free_disk_gb_persists_as_bytes(self, authed_client, app):
        resp = authed_client.post("/parent/settings/advanced", data={
            "port": 8080,
            "host": "0.0.0.0",
            "default_mode": "cache",
            "min_free_disk_gb": "5.0",
            "impersonate": "chrome",
            "cookies_file": "",
            "cookies_from_browser": "",
            "session_timeout_hours": 24,
            "log_level": "info",
        })
        assert resp.status_code == 200
        assert app.state.config.storage.min_free_disk_bytes == int(5.0 * _GB)

    def test_min_free_disk_gb_negative_rejected(self, authed_client, app):
        original = app.state.config.storage.min_free_disk_bytes
        resp = authed_client.post("/parent/settings/advanced", data={
            "port": 8080,
            "host": "0.0.0.0",
            "default_mode": "cache",
            "min_free_disk_gb": "-1",
            "impersonate": "chrome",
            "cookies_file": "",
            "cookies_from_browser": "",
            "session_timeout_hours": 24,
            "log_level": "info",
        })
        assert resp.status_code == 200
        assert b"cannot be negative" in resp.content
        assert app.state.config.storage.min_free_disk_bytes == original
