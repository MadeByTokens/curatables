"""Tests for server startup paths: crash recovery, pending download resume,
abandoned upload sweep."""

from unittest.mock import patch

from app.db.connection import create_connection
from app.db.schema import init_schema
from app.models import Video
from app.repositories import VideoRepository
from app.services.content import ContentService


class TestResumeDownloads:
    def test_pending_downloads_are_kicked_on_startup(self, tmp_path):
        """When ContentService.resume_pending_downloads runs at startup,
        every video in 'pending' with storage_mode='cache' gets its
        background download re-queued via _start_download."""
        db_path = tmp_path / "resume.db"
        conn = create_connection(db_path)
        init_schema(conn)
        repo = VideoRepository(conn)
        repo.insert(Video(
            video_id="pendV1", title="p1", original_title="p1",
            original_url="https://example.com/a",
            download_status="pending", storage_mode="cache",
            resolution="720p",
        ))
        repo.insert(Video(
            video_id="pendV2", title="p2", original_title="p2",
            original_url="https://example.com/b",
            download_status="pending", storage_mode="cache",
            resolution="1080p",
        ))
        # A ready video that should NOT be touched
        repo.insert(Video(
            video_id="readyV", title="r", original_title="r",
            download_status="ready", storage_mode="cache",
        ))
        # An uploaded video in pending should NOT be touched — there's
        # no download path for those.
        repo.insert(Video(
            video_id="upV", title="u", original_title="u",
            download_status="pending", storage_mode="uploaded",
        ))

        with patch.object(ContentService, "_start_download") as mock_start:
            svc = ContentService(
                video_repo=repo, source_repo=None, channel_repo=None,
                source=None, thumbnails=None, storage=None, config=None,
            )
            count = svc.resume_pending_downloads()

        assert count == 2
        assert mock_start.call_count == 2
        called_ids = sorted(c.args[0] for c in mock_start.call_args_list)
        assert called_ids == ["pendV1", "pendV2"]
        conn.close()

    def test_resume_with_no_pending_returns_zero(self, tmp_path):
        db_path = tmp_path / "empty.db"
        conn = create_connection(db_path)
        init_schema(conn)
        repo = VideoRepository(conn)

        with patch.object(ContentService, "_start_download") as mock_start:
            svc = ContentService(
                video_repo=repo, source_repo=None, channel_repo=None,
                source=None, thumbnails=None, storage=None, config=None,
            )
            count = svc.resume_pending_downloads()

        assert count == 0
        assert mock_start.call_count == 0
        conn.close()
