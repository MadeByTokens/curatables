"""Pausing and stopping downloads: what each leaves behind, and the routes."""

import threading
import time
from types import SimpleNamespace

from app.config import Config
from app.db.connection import create_connection, get_db_path
from app.db.schema import init_schema, recover_from_crash
from app.dependencies import get_db
from app.models import Video
from app.repositories import ChannelRepository, SourceRepository, VideoRepository
from app.services import download_control
from app.services.content import ContentService
from app.services.storage import StorageService


class _SlowBackend:
    """Writes a partial file, then waits until told to stop or to finish."""
    last_error = None

    def __init__(self):
        self.started = threading.Event()
        self.finish = threading.Event()

    def download_video(self, url, output_dir, resolution=720, subtitle_langs=None,
                       max_filesize=None, keep_going=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        part = output_dir / "video.f137.mp4.part"
        part.write_bytes(b"x" * 10)
        self.started.set()
        while keep_going():
            if self.finish.is_set():
                part.rename(output_dir / "video.mp4")
                return output_dir / "video.mp4"
            time.sleep(0.01)
        return None


def _setup(tmp_path):
    conn = create_connection(get_db_path(tmp_path))
    init_schema(conn)
    repo = VideoRepository(conn)
    repo.insert(Video(video_id="dl", title="t", original_title="t",
                      original_url="https://example.com/v", download_status="pending"))
    config = Config()
    config.storage.path = str(tmp_path)
    backend = _SlowBackend()
    storage = StorageService(tmp_path, backend=backend)
    svc = ContentService(repo, SourceRepository(conn), ChannelRepository(conn), None,
                         SimpleNamespace(is_cached=lambda vid: True), storage, config)
    svc._start_download = lambda *a, **k: None   # the test runs the job itself
    return conn, repo, svc, storage, backend


def _run(svc):
    job = threading.Thread(target=svc._run_download, args=("dl", "https://example.com/v", "720p"))
    job.start()
    return job


def test_pausing_a_running_download_keeps_its_partial_file(tmp_path):
    conn, repo, svc, storage, backend = _setup(tmp_path)
    job = _run(svc)
    assert backend.started.wait(5)
    assert svc.pause_download("dl")
    job.join(5)
    assert repo.get("dl").download_status == "paused"
    assert (storage.video_dir("dl") / "video.f137.mp4.part").exists()
    assert download_control.wanted("dl") is None

    # A restart keeps it too, and doesn't queue it.
    recover_from_crash(conn, tmp_path)
    assert (storage.video_dir("dl") / "video.f137.mp4.part").exists()
    assert repo.get("dl").download_status == "paused"

    assert svc.retry_download("dl")
    assert repo.get("dl").download_status == "pending"
    backend.started.clear()
    backend.finish.set()
    _run(svc).join(5)
    assert repo.get("dl").download_status == "ready"


def test_stopping_a_running_download_removes_what_it_got(tmp_path):
    conn, repo, svc, storage, backend = _setup(tmp_path)
    job = _run(svc)
    assert backend.started.wait(5)
    assert svc.stop_download("dl")
    job.join(5)
    video = repo.get("dl")
    assert video is not None and video.download_status == "stopped"
    assert list(storage.video_dir("dl").iterdir()) == []
    assert not svc.pause_download("dl")
    assert svc.retry_download("dl") and repo.get("dl").download_status == "pending"


def test_a_queued_download_paused_before_it_starts_never_runs(tmp_path):
    conn, repo, svc, storage, backend = _setup(tmp_path)
    assert svc.pause_download("dl")
    _run(svc).join(5)
    assert not backend.started.is_set()
    assert repo.get("dl").download_status == "paused"
    assert svc.stop_download("dl") and repo.get("dl").download_status == "stopped"


def test_content_page_pause_stop_and_resume(app, authed_client, monkeypatch):
    conn = next(app.dependency_overrides[get_db]())
    repo = VideoRepository(conn)
    for vid in ("p1", "p2", "p3"):
        repo.insert(Video(video_id=vid, title=vid, original_title=vid,
                          original_url="https://example.com/" + vid, download_status="pending"))
    queued = []
    monkeypatch.setattr(ContentService, "_start_download",
                        lambda self, vid, *a, **k: queued.append(vid))
    page = authed_client.get("/parent/content?status=pending").text
    assert "3 downloads waiting or running." in page and 'action="/parent/content/p1/pause"' in page

    authed_client.post("/parent/content/p1/pause")
    authed_client.post("/parent/content/p2/stop")
    assert [repo.get(v).download_status for v in ("p1", "p2", "p3")] == ["paused", "stopped", "pending"]
    page = authed_client.get("/parent/content?status=pending").text
    assert ">Resume</button>" in page and ">Download again</button>" in page
    assert "1 paused." in page

    resp = authed_client.post("/parent/content/downloads/pause-all")
    assert "Paused+1+download" in resp.headers["location"].replace("%20", "+")
    resp = authed_client.post("/parent/content/downloads/resume-all")
    assert [repo.get(v).download_status for v in ("p1", "p3")] == ["pending", "pending"]
    assert sorted(queued) == ["p1", "p3"]

    resp = authed_client.post("/parent/content/bulk", data={"action": "stop",
                                                            "video_ids": ["p1", "p2", "p3"]})
    assert "Stopped 2 downloads." in resp.headers["location"].replace("%20", " ")
    assert repo.get("p3").download_status == "stopped"
