"""Download queue, LRU eviction, crash recovery and re-download behaviour."""

import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.db.schema import recover_from_crash
from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ChannelRepository, ProfileRepository, VideoRepository
from app.services.download_queue import DownloadQueue
from app.services.storage import StorageService
from app.services.thumbnails import ThumbnailService


def _ready(db, video_id, cached_days_ago=40, played_days_ago=None, file_size=100):
    VideoRepository(db).insert(Video(video_id=video_id, title=video_id,
                                      original_title=video_id))
    fmt = "%Y-%m-%d %H:%M:%S"
    cached = (datetime.utcnow() - timedelta(days=cached_days_ago)).strftime(fmt)
    played = (None if played_days_ago is None else
              (datetime.utcnow() - timedelta(days=played_days_ago)).strftime(fmt))
    db.execute("UPDATE videos SET download_status='ready', cached_at=?, "
               "last_played_at=?, file_size=? WHERE video_id=?",
               (cached, played, file_size, video_id))
    db.commit()


class TestDownloadQueue:
    def test_runs_one_job_at_a_time(self):
        queue = DownloadQueue(max_workers=1)
        running = []
        peak = []
        lock = threading.Lock()

        def job():
            with lock:
                running.append(1)
                peak.append(len(running))
            time.sleep(0.02)
            with lock:
                running.pop()

        for i in range(5):
            assert queue.submit(f"v{i}", job)
        queue.wait_idle(timeout=5)
        assert max(peak) == 1

    def test_duplicate_video_is_not_queued_twice(self):
        queue = DownloadQueue(max_workers=1)
        gate = threading.Event()
        assert queue.submit("same", gate.wait)
        assert not queue.submit("same", gate.wait)
        gate.set()
        queue.wait_idle(timeout=5)
        assert queue.submit("same", lambda: None)
        queue.wait_idle(timeout=5)


class TestLruEviction:
    def test_recently_played_video_is_kept(self, db):
        _ready(db, "old_unplayed", cached_days_ago=40)
        _ready(db, "old_favourite", cached_days_ago=40, played_days_ago=1)
        expired = VideoRepository(db).list_expired_cache(cache_days=30)
        assert [v.video_id for v in expired] == ["old_unplayed"]

    def test_eviction_keeps_thumbnail(self, db, tmp_path):
        _ready(db, "thumbkeep", cached_days_ago=40)
        thumbs = ThumbnailService(tmp_path)
        thumbs.get_path("thumbkeep").parent.mkdir(parents=True, exist_ok=True)
        thumbs.get_path("thumbkeep").write_bytes(b"\xff\xd8\xff")
        vdir = tmp_path / "videos" / "thumbkeep"
        vdir.mkdir(parents=True)
        (vdir / "video.mp4").write_bytes(b"x")
        StorageService(tmp_path, backend=None).evict_expired(VideoRepository(db), 30)
        assert not vdir.exists()
        assert thumbs.is_cached("thumbkeep")


class TestStorageFiles:
    def test_non_mp4_download_counts_as_cached(self, tmp_path):
        vdir = tmp_path / "videos" / "webm1"
        vdir.mkdir(parents=True)
        (vdir / "video.webm").write_bytes(b"12345")
        storage = StorageService(tmp_path, backend=None)
        assert storage.is_cached("webm1")
        assert storage.file_size("webm1") == 5


class TestCrashRecovery:
    def test_missing_drive_does_not_reset_library(self, db, tmp_path):
        for i in range(6):
            _ready(db, f"v{i}")
        recover_from_crash(db, tmp_path)
        statuses = {r[0] for r in db.execute("SELECT download_status FROM videos")}
        assert statuses == {"ready"}

    def test_single_missing_file_is_reset(self, db, tmp_path):
        for i in range(6):
            _ready(db, f"v{i}")
            if i:
                d = tmp_path / "videos" / f"v{i}"
                d.mkdir(parents=True)
                (d / "video.mp4").write_bytes(b"x")
        recover_from_crash(db, tmp_path)
        row = db.execute("SELECT download_status FROM videos WHERE video_id='v0'").fetchone()
        assert row[0] == "pending"


class TestRedownload:
    def _setup(self, app, status):
        conn = next(app.dependency_overrides[get_db]())
        ch = ChannelRepository(conn).create("Fun")
        VideoRepository(conn).insert(Video(
            video_id="yt_evicted", title="Dino song", original_title="Dino song",
            channel_id=ch, status="active", download_status=status,
            original_url="https://example.com/v"))
        pid = ProfileRepository(conn).create(Profile(name="k", display_name="K"))
        client = TestClient(app, follow_redirects=False)
        client.post("/profiles/select", data={"profile_id": pid})
        return conn, client

    def test_preparing_page_survives_refresh(self, app):
        conn, client = self._setup(app, "evicted")
        with patch("app.services.content.ContentService._start_download") as start:
            first = client.get("/watch/yt_evicted")
            second = client.get("/watch/yt_evicted")
        assert first.status_code == 200 and "Getting your video ready" in first.text
        assert second.status_code == 200 and "Getting your video ready" in second.text
        assert VideoRepository(conn).get("yt_evicted").download_status == "pending"
        assert start.call_count >= 1

    def test_claim_evicted_is_exclusive(self, db):
        VideoRepository(db).insert(Video(video_id="c1", title="c", original_title="c",
                                         download_status="evicted"))
        repo = VideoRepository(db)
        assert repo.claim_evicted("c1") is True
        assert repo.claim_evicted("c1") is False


def test_sanitised_ids_do_not_collide():
    from app.services.ids import make_video_id
    a = make_video_id("generic", "a.b")
    b = make_video_id("generic", "a-b")
    assert a != b
    assert make_video_id("youtube", "dQw4w9WgXcQ") == "youtube_dQw4w9WgXcQ"
    long1 = make_video_id("generic", "x" * 100 + "1")
    long2 = make_video_id("generic", "x" * 100 + "2")
    assert long1 != long2 and len(long1) <= 110


class TestRedownloadRecheck:
    def _service(self, db, tmp_path, new_seconds):
        from types import SimpleNamespace
        from app.config import Config
        from app.repositories import ChannelRepository, SourceRepository
        from app.services.content import ContentService

        config = Config()
        config.storage.path = str(tmp_path)
        out = tmp_path / "videos" / "rv" / "video.mp4"
        out.parent.mkdir(parents=True)
        out.write_bytes(b"x")

        class FakeStorage:
            backend = SimpleNamespace(last_error=None)
            normalizer = SimpleNamespace(probe=SimpleNamespace(
                probe=lambda p: SimpleNamespace(duration_seconds=new_seconds)))

            def check_can_write(self, required_bytes=0):
                return True, ""

            def download(self, *a, **k):
                return out

            def file_size(self, vid):
                return 1

        thumbs = SimpleNamespace(is_cached=lambda vid: True)
        return ContentService(VideoRepository(db), SourceRepository(db),
                              ChannelRepository(db), None, thumbs, FakeStorage(), config)

    def test_changed_length_goes_back_to_review(self, tmp_path):
        from app.db.connection import create_connection, get_db_path
        from app.db.schema import init_schema
        conn = create_connection(get_db_path(tmp_path))
        init_schema(conn)
        VideoRepository(conn).insert(Video(video_id="rv", title="t", original_title="t",
                                           duration=120, download_status="pending"))
        svc = self._service(conn, tmp_path, new_seconds=600)
        svc._run_download("rv", "https://example.com/v", "720p", recheck=True)
        row = VideoRepository(conn).get("rv")
        assert row.download_status == "ready"
        assert row.status == "review"

    def test_same_length_stays_active(self, tmp_path):
        from app.db.connection import create_connection, get_db_path
        from app.db.schema import init_schema
        conn = create_connection(get_db_path(tmp_path))
        init_schema(conn)
        VideoRepository(conn).insert(Video(video_id="rv", title="t", original_title="t",
                                           duration=120, download_status="pending"))
        svc = self._service(conn, tmp_path, new_seconds=121)
        svc._run_download("rv", "https://example.com/v", "720p", recheck=True)
        assert VideoRepository(conn).get("rv").status == "active"


def test_video_deleted_during_download_has_its_files_removed(tmp_path):
    from types import SimpleNamespace
    from app.config import Config
    from app.db.connection import create_connection, get_db_path
    from app.db.schema import init_schema
    from app.repositories import ChannelRepository, SourceRepository
    from app.services.content import ContentService

    conn = create_connection(get_db_path(tmp_path))
    init_schema(conn)
    repo = VideoRepository(conn)
    repo.insert(Video(video_id="gone", title="t", original_title="t",
                      download_status="pending"))
    removed = []

    class FakeStorage:
        backend = SimpleNamespace(last_error=None)

        def check_can_write(self, required_bytes=0):
            return True, ""

        def download(self, *a, **k):
            repo.delete("gone")
            return tmp_path / "video.mp4"

        def delete_video_files(self, video):
            removed.append(video.video_id)

    config = Config()
    config.storage.path = str(tmp_path)
    svc = ContentService(repo, SourceRepository(conn), ChannelRepository(conn), None,
                         SimpleNamespace(), FakeStorage(), config)
    svc._run_download("gone", "https://example.com/v", "720p")
    assert removed == ["gone"]

def test_tus_append_cannot_exceed_declared_length(tmp_path):
    import pytest
    from app.services.uploads import UploadOffsetMismatch, UploadService
    storage = StorageService(tmp_path, backend=None)
    uploads = UploadService(storage, None, None, None)
    session = uploads.create("a.mp4", total_size=4, channel_id=1, title="")
    assert uploads.append(session.token, 0, b"abcd") == 4
    with pytest.raises(UploadOffsetMismatch):
        uploads.append(session.token, 4, b"e")


def test_preview_thumbnails_download_concurrently(tmp_path, monkeypatch):
    import threading
    import time
    from app.services.thumbnails import ThumbnailService

    svc = ThumbnailService(tmp_path)
    active, peak, lock = [0], [0], threading.Lock()

    def slow_download(video_id, url):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.05)
        with lock:
            active[0] -= 1

    monkeypatch.setattr(svc, "download", slow_download)
    svc.download_many([(f"v{i}", f"https://t.invalid/{i}.jpg") for i in range(8)])
    assert peak[0] > 1


class TestLongUse:
    """Leftovers that would pile up on a box that runs for months."""

    def test_a_power_cut_mid_conversion_leaves_nothing_behind(self, db, tmp_path):
        vdir = tmp_path / "videos" / "cut"
        vdir.mkdir(parents=True)
        for name in ("video.webm", "video.norm.mp4", "video.norm.mp4.log",
                     "video.f137.mp4.part", "video.f137.mp4.part-Frag3", "video.f137.mp4.ytdl"):
            (vdir / name).write_bytes(b"x")
        recover_from_crash(db, tmp_path)
        assert sorted(p.name for p in vdir.iterdir()) == ["video.webm"]

    def test_an_unconverted_original_is_converted_before_it_counts(self, tmp_path):
        class Normalizer:
            def __init__(self):
                self.seen = []

            def normalize(self, path, space=None):
                self.seen.append(path.name)
                target = path.with_name("video.mp4")
                path.replace(target)
                return type("R", (), {"path": target})()

        vdir = tmp_path / "videos" / "orig"
        vdir.mkdir(parents=True)
        (vdir / "video.webm").write_bytes(b"x")
        normalizer = Normalizer()
        storage = StorageService(tmp_path, backend=None, normalizer=normalizer)
        assert storage.download("orig", "https://example.com/v").name == "video.mp4"
        assert normalizer.seen == ["video.webm"]

    def test_a_cut_off_thumbnail_is_not_cached(self, tmp_path):
        svc = ThumbnailService(tmp_path)
        path = svc.get_path("v1")
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
        assert not svc.is_cached("v1")
        svc.save_uploaded("v1", b"\xff\xd8\xff")
        assert svc.is_cached("v1")
        assert [p.name for p in path.parent.iterdir()] == ["v1.jpg"]

    def test_a_failed_frame_leaves_no_thumbnail(self, tmp_path, monkeypatch):
        import subprocess as sp
        src = tmp_path / "videos" / "v2" / "video.mp4"
        src.parent.mkdir(parents=True)
        src.write_bytes(b"x")

        def ffmpeg_dies(args, **kw):
            Path(args[-1]).write_bytes(b"")
            return sp.CompletedProcess(args, 1)

        monkeypatch.setattr("app.services.thumbnails.subprocess.run", ffmpeg_dies)
        svc = ThumbnailService(tmp_path)
        assert svc.extract_frame("v2") is None
        assert list(svc.get_path("v2").parent.iterdir()) == []

    def test_deleting_a_video_deletes_its_comments_and_reactions(self, db):
        from app.repositories.comment_repo import CommentRepository
        from app.repositories.reaction_repo import ReactionRepository
        pid = ProfileRepository(db).create(Profile(name="k", display_name="K"))
        VideoRepository(db).insert(Video(video_id="gone", title="t", original_title="t"))
        CommentRepository(db).create("gone", "hi", pid)
        ReactionRepository(db).upsert(pid, "gone", "👍")
        VideoRepository(db).delete("gone")
        assert db.execute("SELECT COUNT(*) FROM comments").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM reactions").fetchone()[0] == 0

    def test_upload_leftovers_are_swept_only_when_idle(self, tmp_path):
        import os
        storage = StorageService(tmp_path, backend=None)
        tmp = storage.upload_tmp_dir()
        old = time.time() - 48 * 3600
        abandoned = tmp / "abandoned.work"
        busy = tmp / "busy.work"
        for d in (abandoned, busy):
            d.mkdir()
            (d / "out.mp4").write_bytes(b"x")
            os.utime(d / "out.mp4", (old, old))
            os.utime(d, (old, old))
        os.utime(busy / "out.mp4")  # still being written
        assert storage.sweep_upload_tmp(ttl_hours=24) == 1
        assert not abandoned.exists() and busy.exists()

    def test_the_periodic_sweep_keeps_folders_of_live_imports(self, db, tmp_path):
        import os
        from types import SimpleNamespace
        from app.services.imports import ImportWorker
        tmp = tmp_path / "uploads" / ".tmp"
        old = time.time() - 48 * 3600
        for name in ("import-1", "import-preview-2", "import-3"):
            (tmp / name).mkdir(parents=True)
            os.utime(tmp / name, (old, old))
        (tmp / "import-4").mkdir()  # just created, no job row yet
        db.execute("INSERT INTO import_jobs (id, state, batch, source_path) VALUES (1, 'queued', 'b', '/x')")
        db.execute("INSERT INTO import_jobs (id, state, batch, source_path) VALUES (2, 'queued', 'b', '/x')")
        db.commit()
        worker = ImportWorker(SimpleNamespace(media_dir=tmp_path), None, None, None)
        assert worker.sweep(db, idle_hours=24) == 1
        assert sorted(p.name for p in tmp.iterdir()) == ["import-1", "import-4", "import-preview-2"]
