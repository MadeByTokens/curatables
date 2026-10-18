"""Long use: a drive that fills up, a clock that is behind, the family's day."""

import logging
import os
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.dependencies import get_db
from app.models import Profile, Video
from app.repositories import ProfileRepository, VideoRepository
from app.services import disk_space
from app.services.clock import ClockService
from app.services.disk_space import InsufficientDiskSpace, SpaceWatch
from app.services.family_time import since_days_ago, utc_start_of, valid_timezone
from app.services.ffmpeg_runner import CancelToken, run_ffmpeg
from app.services.imports import copy_with_progress
from app.services.storage import StorageService
from app.template_utils import friendly_date
from tests.fake_helper import FakeHelper
from tests.test_uploads import _encode_metadata, _get_channel_id

GB = 1024 ** 3


def _conn(app):
    return next(app.dependency_overrides[get_db]())


@pytest.fixture
def free(monkeypatch):
    """Pretend the drive has `free.bytes` free."""
    class Free:
        bytes = 100 * GB
    monkeypatch.setattr(disk_space, "free_bytes", lambda path: Free.bytes)
    return Free


# --- the drive never fills up ----------------------------------------------------

def test_a_space_watch_trips_under_the_minimum_and_stays_tripped(free):
    watch = SpaceWatch(Path("/"), 2 * GB, every=0)
    assert watch.ok()
    free.bytes = 1 * GB
    assert not watch.ok()
    free.bytes = 50 * GB
    assert not watch.ok()
    with pytest.raises(InsufficientDiskSpace):
        watch.check()


class _FillingBackend:
    """Writes part of a download, then the drive fills up."""
    last_error = None

    def __init__(self, free):
        self.free = free

    def download_video(self, url, output_dir, resolution=720, subtitle_langs=None,
                       max_filesize=None, keep_going=None):
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "video.f137.mp4.part").write_bytes(b"x" * 10)
        self.free.bytes = 1 * GB
        assert keep_going is not None
        while keep_going():
            time.sleep(0.01)
        return None


def test_a_download_stops_when_the_drive_fills_and_leaves_nothing(tmp_path, free):
    storage = StorageService(tmp_path, backend=_FillingBackend(free), min_free_bytes=2 * GB)
    with pytest.raises(InsufficientDiskSpace):
        storage.download("vid", "https://example.com/v")
    assert list(storage.video_dir("vid").iterdir()) == []


def test_a_conversion_stops_when_the_drive_fills(tmp_path, free):
    slow = tmp_path / "fake-ffmpeg"
    slow.write_text("#!/bin/sh\nsleep 30\n")
    slow.chmod(0o755)
    free.bytes = 1 * GB
    started = time.monotonic()
    with pytest.raises(InsufficientDiskSpace):
        run_ffmpeg([str(slow)], duration=10, log_path=tmp_path / "log",
                   space=SpaceWatch(tmp_path, 2 * GB, every=0))
    assert time.monotonic() - started < 10


def test_an_import_copy_stops_when_the_drive_fills(tmp_path, free):
    src = tmp_path / "movie.mp4"
    src.write_bytes(b"x" * 100)
    free.bytes = 1 * GB
    with pytest.raises(InsufficientDiskSpace):
        copy_with_progress(src, tmp_path / "copy.mp4", lambda p: None, CancelToken(),
                           SpaceWatch(tmp_path, 2 * GB, every=0))
    assert not (tmp_path / "copy.mp4").exists()


def test_an_upload_piece_that_does_not_fit_is_refused(app, authed_client, monkeypatch):
    app.state.config.storage.min_free_disk_bytes = 2 * GB
    free = {"bytes": 2 * GB + 100}
    monkeypatch.setattr(StorageService, "get_free_space", lambda self: free["bytes"])
    created = authed_client.post("/parent/upload/tus", headers={
        "Tus-Resumable": "1.0.0", "Upload-Length": "100",
        "Upload-Metadata": _encode_metadata({"filename": "a.mp4",
                                             "channel_id": str(_get_channel_id(app))})})
    assert created.status_code == 201, created.text
    url = created.headers["location"]
    free["bytes"] = 2 * GB + 10  # something else filled the drive meanwhile
    resp = authed_client.patch(url, content=b"x" * 100, headers={
        "Tus-Resumable": "1.0.0", "Upload-Offset": "0",
        "Content-Type": "application/offset+octet-stream"})
    assert resp.status_code == 507


def test_a_backup_never_fills_the_sd_card(tmp_path):
    import subprocess
    data = tmp_path / "data"
    (data / "db").mkdir(parents=True)
    import sqlite3
    sqlite3.connect(data / "db" / "curatables.db").execute("CREATE TABLE t (x)").connection.close()
    script = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"
    env = dict(os.environ, CURATABLES_DATA_DIR=str(data), PYTHON=sys.executable,
               MIN_FREE_MB=str(10 ** 9))
    proc = subprocess.run(["bash", str(script), str(tmp_path / "out")], env=env,
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode != 0
    assert "not enough free space" in proc.stderr
    assert list((tmp_path / "out").iterdir()) == []


def test_the_dashboard_warns_when_the_sd_card_is_low(app, authed_client, monkeypatch):
    monkeypatch.setitem(app.state.templates.env.globals, "system_disk_status",
                        lambda: {"free_gb": 0.4, "free_percent": 2, "status": "blocked"})
    page = authed_client.get("/parent/").text
    assert "memory card is almost full" in page


# --- picking what nobody watches -------------------------------------------------

def _ready(conn, video_id, plays=0, size=0, storage_mode="cache", played=None):
    VideoRepository(conn).insert(Video(video_id=video_id, title=f"T {video_id}",
                                       original_title=video_id, storage_mode=storage_mode))
    conn.execute("UPDATE videos SET download_status = 'ready', play_count = ?, file_size = ?, "
                 "last_played_at = ? WHERE video_id = ?", (plays, size, played, video_id))
    conn.commit()


def test_playing_a_video_counts(app):
    conn = _conn(app)
    _ready(conn, "v1")
    repo = VideoRepository(conn)
    repo.touch_played("v1")
    repo.touch_played("v1")
    assert repo.get("v1").play_count == 2


def test_the_play_count_migration_counts_the_history(tmp_path):
    import importlib.util
    import sqlite3
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE videos (video_id TEXT)")
    conn.execute("CREATE TABLE events (event_type TEXT, video_id TEXT, timestamp TEXT)")
    conn.execute("INSERT INTO videos VALUES ('a'), ('b')")
    conn.executemany("INSERT INTO events VALUES (?, ?, '2026-01-01 00:00:00')",
                     [("video_play", "a"), ("video_play", "a"), ("video_view", "a")])
    path = Path(__file__).resolve().parents[1] / "app/db/migrations/0014_play_counts.py"
    spec = importlib.util.spec_from_file_location("m", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.up(conn)
    module.up(conn)  # idempotent
    counts = dict(conn.execute("SELECT video_id, play_count FROM videos").fetchall())
    assert counts == {"a": 2, "b": 0}


def test_the_content_list_sorts_by_least_watched(app, authed_client):
    conn = _conn(app)
    _ready(conn, "popular", plays=9)
    _ready(conn, "ignored", plays=0)
    _ready(conn, "sometimes", plays=3)
    page = authed_client.get("/parent/content?sort=least_played").text
    order = [page.index(f'data-video-id="{v}"') for v in ("ignored", "sometimes", "popular")]
    assert order == sorted(order)
    assert "Watched 9 times" in page


def test_freeing_files_keeps_the_videos_listed(app, authed_client):
    conn = _conn(app)
    media = app.state.config.media_dir
    _ready(conn, "dl", size=5)
    (media / "videos" / "dl").mkdir(parents=True)
    (media / "videos" / "dl" / "video.mp4").write_bytes(b"x" * 5)
    _ready(conn, "up", size=5, storage_mode="uploaded")
    resp = authed_client.post("/parent/content/bulk", data={
        "video_ids": ["dl", "up"], "action": "free", "page": "1", "sort": "least_played"})
    assert resp.status_code == 302
    assert "sort=least_played" in resp.headers["location"]
    repo = VideoRepository(conn)
    assert repo.get("dl").download_status == "evicted"
    assert not (media / "videos" / "dl" / "video.mp4").exists()
    assert repo.get("up").download_status == "ready"


# --- the clock -------------------------------------------------------------------

def test_a_kids_device_sets_a_clock_that_is_behind():
    helper = FakeHelper(clock_status={"synchronized": False},
                        set_clock=lambda p: {"set": True, "step": 3600})
    clock = ClockService(helper)
    assert clock.wants_browser_time() and not clock.trusted()
    assert clock.offer(time.time() + 3600) == "set"
    assert ("set_clock", {"epoch": pytest.approx(int(time.time() + 3600), abs=2)}) in helper.calls
    assert not clock.wants_browser_time()
    # Eviction waits for NTP or a parent's device to vouch for it.
    assert not clock.trusted() and clock.wants_browser_time(parent=True)
    assert clock.offer(time.time() + 5, by_parent=True) == "close enough"
    assert clock.trusted() and not clock.wants_browser_time(parent=True)


def test_a_visitor_behind_the_box_changes_nothing():
    helper = FakeHelper(clock_status={"synchronized": False})
    clock = ClockService(helper)
    assert clock.offer(time.time() - 86400) == "behind"
    assert "set_clock" not in helper.actions()


def test_a_close_clock_is_trusted_without_changing_it():
    helper = FakeHelper(clock_status={"synchronized": False})
    clock = ClockService(helper)
    assert clock.offer(time.time() + 5, by_parent=True) == "close enough"
    assert clock.trusted() and "set_clock" not in helper.actions()


def test_a_synced_box_asks_nobody():
    clock = ClockService(FakeHelper(clock_status={"synchronized": True}))
    assert not clock.wants_browser_time()
    assert clock.offer(time.time() + 86400) == "not needed"


def test_pages_load_the_clock_script_only_when_the_box_asks(app, client):
    app.state.clock = ClockService(FakeHelper(clock_status={"synchronized": False}))
    assert "/static/js/clock.js" in client.get("/profiles").text
    app.state.clock = ClockService(FakeHelper(clock_status={"synchronized": True}))
    assert "/static/js/clock.js" not in client.get("/profiles").text


def test_only_signed_in_visitors_set_the_clock(app, client):
    helper = FakeHelper(clock_status={"synchronized": False},
                        set_clock=lambda p: {"set": True, "step": 3600})
    app.state.clock = ClockService(helper)
    client.post("/api/clock", data={"now": str((time.time() + 3600) * 1000)})
    assert "set_clock" not in helper.actions()


def test_cache_eviction_waits_for_a_trusted_clock(app, monkeypatch):
    from app import lifecycle
    called = []
    monkeypatch.setattr(StorageService, "evict_expired",
                        lambda self, repo, days: called.append(days))
    config = app.state.config
    config.storage.cache_days = 7
    untrusted = ClockService(FakeHelper(clock_status={"synchronized": False}))
    lifecycle._maintenance_pass(config, None, untrusted)
    assert called == []
    lifecycle._maintenance_pass(config, None, None)
    assert called == [7]


# --- the family's day ------------------------------------------------------------

def test_the_beacon_records_a_parents_time_zone_once(app, client, authed_client):
    app.state.config.server.timezone = ""
    client.post("/api/clock", data={"now": "0", "tz": "Europe/Lisbon"})
    assert app.state.config.server.timezone == ""  # not a parent
    authed_client.post("/api/clock", data={"now": "0", "tz": "America/New_York"})
    assert app.state.config.server.timezone == "America/New_York"
    authed_client.post("/api/clock", data={"now": "0", "tz": "Asia/Tokyo"})
    assert app.state.config.server.timezone == "America/New_York"


def test_today_starts_at_the_familys_midnight():
    assert utc_start_of(date(2026, 10, 16), "America/New_York") == "2026-10-16 04:00:00"
    late_evening = datetime(2026, 10, 17, 2, 30, tzinfo=timezone.utc)  # 22:30 in New York
    assert since_days_ago(0, "America/New_York", now=late_evening) == "2026-10-16 04:00:00"
    assert not valid_timezone("../../etc/passwd") and not valid_timezone("Mars/Base")


def test_dates_are_shown_in_the_familys_day():
    today = date(2026, 10, 16)
    # 01:30 UTC on the 17th is still the 16th in New York.
    assert friendly_date("2026-10-17 01:30:00", today, tz="America/New_York") == "today"
    assert friendly_date("2026-10-16 03:30:00", today, tz="America/New_York") == "yesterday"


def test_settings_change_the_time_zone(app, authed_client):
    base = {"cache_days": "0", "default_resolution": "720p"}
    authed_client.post("/parent/settings", data={**base, "timezone": "Europe/Paris"})
    assert app.state.config.server.timezone == "Europe/Paris"
    page = authed_client.post("/parent/settings", data={**base, "timezone": "Nowhere/Land"}).text
    assert app.state.config.server.timezone == "Europe/Paris"
    assert "not a time zone" in page


# --- small leftovers -------------------------------------------------------------

def test_deleting_a_profile_deletes_its_pictures(app, authed_client):
    conn = _conn(app)
    pid = ProfileRepository(conn).create(Profile(name="k", display_name="K"))
    pics = app.state.config.media_dir / "thumbnails" / "profiles" / str(pid)
    pics.mkdir(parents=True)
    (pics / "v.jpg").write_bytes(b"x")
    assert authed_client.post(f"/parent/profiles/{pid}/delete").status_code == 302
    assert not pics.exists()


def test_the_log_file_leaves_out_access_lines(tmp_path):
    from app.logging_config import setup_logging
    root = logging.getLogger()
    saved = root.handlers[:], root.level
    try:
        setup_logging("info", tmp_path)
        logging.getLogger("curatables.access").info("GET / 200 3ms")
        logging.getLogger("curatables").info("downloaded a video")
        for handler in root.handlers:
            handler.flush()
        text = (tmp_path / "curatables.log").read_text()
    finally:
        for handler in root.handlers:
            handler.close()
        root.handlers[:], root.level = saved[0], saved[1]
    assert "downloaded a video" in text and "GET / 200" not in text


def test_stored_times_show_in_the_familys_day_and_time():
    from app.template_utils import local_time
    today = date(2026, 10, 16)
    assert local_time("2026-10-16 22:36:14", today, tz="America/New_York") == "today 18:36"
    assert local_time("2026-10-17T00:40:09.5+00:00", today, tz="America/New_York") == "today 20:40"
    assert local_time("2026-09-01T13:10:00Z", today, tz="America/New_York") == "1 Sep 2026 09:10"
    assert local_time("", today) == ""
