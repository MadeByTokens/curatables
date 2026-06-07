"""Cache eviction sweep — repository query + storage service orchestration."""

import shutil
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from app.models import Video
from app.repositories import VideoRepository
from app.services.storage import StorageService, EvictionReport


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _insert_ready(db, video_id, *, cached_days_ago=None,
                  storage_mode="cache", keep_forever=False,
                  download_status="ready", file_size=1024):
    """Insert a video and set cache-related fields directly (bypasses
    the normal add flow so we can control cached_at deterministically)."""
    repo = VideoRepository(db)
    repo.insert(Video(
        video_id=video_id,
        title=f"video {video_id}",
        original_title=f"video {video_id}",
        storage_mode=storage_mode,
        download_status="pending",  # insert defaults
    ))
    # Now backfill cache fields + keep_forever directly.
    cached_at = None
    if cached_days_ago is not None:
        cached_at = _iso(datetime.utcnow() - timedelta(days=cached_days_ago))
    db.execute(
        "UPDATE videos SET download_status=?, storage_mode=?, cached_at=?, "
        "keep_forever=?, file_size=? WHERE video_id=?",
        (download_status, storage_mode, cached_at,
         1 if keep_forever else 0, file_size, video_id),
    )
    db.commit()


class TestListExpiredCacheQuery:
    def test_expired_cache_video_matches(self, db):
        _insert_ready(db, "youtube_old", cached_days_ago=40)
        repo = VideoRepository(db)
        expired = repo.list_expired_cache(cache_days=30)
        assert [v.video_id for v in expired] == ["youtube_old"]

    def test_recent_cache_video_skipped(self, db):
        _insert_ready(db, "youtube_recent", cached_days_ago=5)
        repo = VideoRepository(db)
        assert repo.list_expired_cache(cache_days=30) == []

    def test_keep_forever_skipped(self, db):
        _insert_ready(db, "youtube_pinned", cached_days_ago=40, keep_forever=True)
        repo = VideoRepository(db)
        assert repo.list_expired_cache(cache_days=30) == []

    def test_uploaded_never_evicted(self, db):
        _insert_ready(db, "local_upload", cached_days_ago=400,
                      storage_mode="uploaded")
        repo = VideoRepository(db)
        assert repo.list_expired_cache(cache_days=30) == []

    def test_not_ready_skipped(self, db):
        _insert_ready(db, "yt_pending", cached_days_ago=40,
                      download_status="pending")
        _insert_ready(db, "yt_downloading", cached_days_ago=40,
                      download_status="downloading")
        _insert_ready(db, "yt_error", cached_days_ago=40,
                      download_status="error")
        repo = VideoRepository(db)
        assert repo.list_expired_cache(cache_days=30) == []

    def test_null_cached_at_skipped(self, db):
        _insert_ready(db, "never_cached")  # cached_days_ago=None → NULL
        # download_status was set to 'ready' but cached_at is NULL —
        # an inconsistent state we defensively skip.
        repo = VideoRepository(db)
        assert repo.list_expired_cache(cache_days=30) == []

    def test_cache_days_zero_short_circuits(self, db):
        _insert_ready(db, "yt_ancient", cached_days_ago=10000)
        repo = VideoRepository(db)
        assert repo.list_expired_cache(cache_days=0) == []


class TestMarkEvicted:
    def test_mark_evicted_transitions_row(self, db):
        _insert_ready(db, "yt_evict", cached_days_ago=40)
        repo = VideoRepository(db)
        repo.mark_evicted("yt_evict")
        v = repo.get("yt_evict")
        assert v.download_status == "evicted"
        assert v.cached_at is None
        assert v.cache_expires_at is None
        assert v.file_size == 0


class TestStorageServiceEvictExpired:

    def test_evicts_expired_and_deletes_files(self, db, tmp_path):
        _insert_ready(db, "yt_old", cached_days_ago=40, file_size=9999)
        # Drop a real file on disk so we can observe deletion.
        vdir = tmp_path / "videos" / "yt_old"
        vdir.mkdir(parents=True)
        (vdir / "video.mp4").write_bytes(b"x" * 10)

        storage = StorageService(tmp_path, backend=None)
        repo = VideoRepository(db)
        report = storage.evict_expired(repo, cache_days=30)

        assert report.evicted_count == 1
        assert report.freed_bytes == 9999
        assert report.video_ids == ["yt_old"]
        assert not vdir.exists()
        assert repo.get("yt_old").download_status == "evicted"

    def test_no_op_when_cache_days_zero(self, db, tmp_path):
        _insert_ready(db, "yt_old", cached_days_ago=40)
        storage = StorageService(tmp_path, backend=None)
        repo = VideoRepository(db)
        report = storage.evict_expired(repo, cache_days=0)
        assert report.evicted_count == 0
        assert repo.get("yt_old").download_status == "ready"

    def test_skips_keep_forever(self, db, tmp_path):
        _insert_ready(db, "pinned", cached_days_ago=40, keep_forever=True)
        storage = StorageService(tmp_path, backend=None)
        repo = VideoRepository(db)
        report = storage.evict_expired(repo, cache_days=30)
        assert report.evicted_count == 0
        assert repo.get("pinned").download_status == "ready"
        assert repo.get("pinned").keep_forever is True

    def test_skips_uploaded(self, db, tmp_path):
        _insert_ready(db, "uploaded", cached_days_ago=400,
                      storage_mode="uploaded")
        storage = StorageService(tmp_path, backend=None)
        repo = VideoRepository(db)
        report = storage.evict_expired(repo, cache_days=30)
        assert report.evicted_count == 0
        assert repo.get("uploaded").download_status == "ready"

    def test_partial_failure_does_not_stop_sweep(self, db, tmp_path):
        # Two expired rows; we corrupt the mark_evicted path on the
        # first to prove the second still evicts.
        _insert_ready(db, "fail_row", cached_days_ago=40)
        _insert_ready(db, "ok_row", cached_days_ago=41)

        storage = StorageService(tmp_path, backend=None)
        repo = VideoRepository(db)

        original_mark = repo.mark_evicted
        def flaky_mark(video_id):
            if video_id == "fail_row":
                raise RuntimeError("simulated I/O error")
            return original_mark(video_id)
        repo.mark_evicted = flaky_mark

        report = storage.evict_expired(repo, cache_days=30)
        assert "ok_row" in report.video_ids
        assert "fail_row" not in report.video_ids
        assert repo.get("ok_row").download_status == "evicted"
        assert repo.get("fail_row").download_status == "ready"  # unchanged
