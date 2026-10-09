"""Import from a drive: browse, details, picture lookup, background jobs
with live progress, and a clean cancel at any point."""
import itertools
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from app.dependencies import get_db, get_lookup_service
from app.repositories import ChannelRepository, VideoRepository
from app.repositories.import_job_repo import ImportJobRepository
from app.services.imports import build_worker, work_dir_name
from app.services.lookup import Candidate

pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="ffmpeg/ffprobe not installed")

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 200


class _Shared:
    """The test's single connection, handed to background threads; they
    may not close it."""

    def __init__(self, conn):
        self._conn = conn

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._conn, name)


_TONES = itertools.count(300, 37)


def _clip(path: Path, *, seconds=1, size="320x240", vcodec="libx264", extra=()):
    """A short test clip; every clip gets its own tone, so no two files are
    the same bytes (imports dedupe by content)."""
    args = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc=size={size}:rate=25:duration={seconds}",
            "-f", "lavfi", "-i", f"sine=frequency={next(_TONES)}:duration={seconds}"]
    if vcodec == "libx264":
        args += ["-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p"]
    else:
        args += ["-c:v", vcodec, "-deadline", "realtime", "-cpu-used", "8", "-b:v", "200k"]
    args += ["-c:a", "aac" if path.suffix != ".webm" else "libopus", *extra, str(path)]
    subprocess.run(args, check=True)
    return path


@pytest.fixture
def drive(app, tmp_path_factory):
    root = tmp_path_factory.mktemp("drive")
    movies = root / "Movies"
    show = root / "Bluey" / "Season 1"
    movies.mkdir(parents=True)
    show.mkdir(parents=True)
    _clip(movies / "Finding.Nemo.2003.1080p.mp4", extra=("-movflags", "+faststart"))
    (movies / "poster.jpg").write_bytes(JPEG)
    (movies / "Finding.Nemo.2003.1080p.en.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nJust keep swimming\n")
    _clip(movies / "Old.Cartoon.webm", vcodec="libvpx-vp9")
    for n in (2, 1):
        _clip(show / f"Bluey.S01E0{n}.mp4", extra=("-movflags", "+faststart"))
    (root / ".hidden.mp4").write_bytes(b"x")
    (root / "link.mp4").symlink_to(movies / "Old.Cartoon.webm")
    config = app.state.config
    config.storage.import_roots = [str(root)]
    conn = next(app.dependency_overrides[get_db]())
    app.state.db_connect = lambda: _Shared(conn)
    yield root
    config.storage.import_roots = []


def _conn(app):
    return next(app.dependency_overrides[get_db]())


def _wait_ready(app, batch, timeout=60):
    repo = ImportJobRepository(_conn(app))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        jobs = [j for j in repo.list_batch(batch) if j.state == "draft"]
        if jobs and all(j.details.get("ready") for j in jobs):
            return jobs
        time.sleep(0.1)
    raise AssertionError("batch never got ready")


def _run_all(app):
    worker = build_worker(app.state.config, app.state.db_connect)
    while worker._run_next():
        pass


def _select(authed_client, app, files=None, whole=False):
    data = {"root": "0"}
    if whole:
        data.update(whole_folder="1", folder="")
    resp = authed_client.post("/parent/import/select",
                              data={**data, "files": files or []}, follow_redirects=False)
    assert resp.status_code == 303, resp.text
    batch = resp.headers["location"].rsplit("/", 1)[-1]
    return batch, _wait_ready(app, batch)


def test_browse_lists_only_videos_inside_the_import_folder(app, authed_client, drive):
    page = authed_client.get("/parent/import/browse?root=0").text
    assert "Movies" in page and "Bluey" in page
    assert ".hidden" not in page and "link.mp4" not in page
    page = authed_client.get("/parent/import/browse?root=0&path=Movies").text
    assert "Finding.Nemo.2003.1080p.mp4" in page and "poster.jpg" not in page
    for bad in ("..", "../..", "/etc", "Movies/../../.."):
        resp = authed_client.get(f"/parent/import/browse?root=0&path={bad}",
                                 follow_redirects=False)
        assert resp.status_code == 303 and "error=" in resp.headers["location"]
    resp = authed_client.post("/parent/import/select",
                              data={"root": "0", "files": ["../../etc/passwd"]},
                              follow_redirects=False)
    assert "error=" in resp.headers["location"]


def test_media_folder_is_never_an_import_root(app, authed_client, drive):
    app.state.config.storage.import_roots = [str(app.state.config.media_dir)]
    assert "the library&#39;s own folder" in authed_client.get("/parent/import").text
    assert authed_client.get("/parent/import/browse?root=0",
                             follow_redirects=False).status_code == 303


def test_details_are_guessed_from_names_and_sidecars(app, authed_client, drive):
    batch, jobs = _select(authed_client, app, whole=True)
    by_name = {j.name: j for j in jobs}
    assert set(by_name) == {"Finding.Nemo.2003.1080p.mp4", "Old.Cartoon.webm",
                            "Bluey.S01E01.mp4", "Bluey.S01E02.mp4"}
    nemo = by_name["Finding.Nemo.2003.1080p.mp4"].details
    assert (nemo["title"], nemo["year"], nemo["origin"]) == ("Finding Nemo", 2003, "movie")
    assert nemo["probe"]["plan"] == "none"
    assert nemo["picture_from"] == "sidecar"
    assert [(s["kind"], s["lang"], s["on"]) for s in nemo["subtitles"]] == [("file", "en", True)]
    assert by_name["Old.Cartoon.webm"].details["probe"]["plan"] == "transcode"
    ep = by_name["Bluey.S01E02.mp4"].details
    assert (ep["show"], ep["season"], ep["episode"]) == ("Bluey", 1, 2)

    page = authed_client.get(f"/parent/import/batch/{batch}").text
    assert "look like episodes of <strong>Bluey</strong>" in page
    assert "Plays as it is" in page and "Converted for the player" in page


def test_import_copies_converts_and_groups_a_show(app, authed_client, drive):
    family = ChannelRepository(_conn(app)).create("Family")
    batch, jobs = _select(authed_client, app, whole=True)
    form = {"action": "show:Bluey"}
    for j in jobs:
        form[f"j{j.id}_title"] = j.details["title"]
        form[f"j{j.id}_channel_id"] = "" if j.details.get("episode") else str(family)
    nemo_job = next(j for j in jobs if j.name.startswith("Finding.Nemo"))
    form[f"j{nemo_job.id}_subtitles"] = "0"
    resp = authed_client.post(f"/parent/import/batch/{batch}", data=form, follow_redirects=False)
    assert resp.headers["location"] == f"/parent/import/batch/{batch}"
    page = authed_client.get(f"/parent/import/batch/{batch}").text
    assert "Keep them separate" in page

    form["action"] = "import"
    form["mode"] = "copy"
    resp = authed_client.post(f"/parent/import/batch/{batch}", data=form, follow_redirects=False)
    assert resp.headers["location"] == "/parent/import"
    status = authed_client.get("/parent/import/status.json").json()
    assert status["active"] == 4 and {j["line"] for j in status["jobs"]} == {"Waiting its turn"}

    _run_all(app)

    repo = ImportJobRepository(_conn(app))
    done = {j.name: j for j in repo.list_states(("done", "failed"))}
    assert {j.state for j in done.values()} == {"done"}, [j.error for j in done.values()]
    videos = VideoRepository(_conn(app))
    nemo = videos.get(done["Finding.Nemo.2003.1080p.mp4"].video_id)
    assert (nemo.title, nemo.origin, nemo.channel_id) == ("Finding Nemo", "movie", family)
    assert nemo.source_path.endswith("Finding.Nemo.2003.1080p.mp4")
    media = app.state.config.media_dir
    nemo_dir = media / "uploads" / nemo.video_id
    assert (nemo_dir / "video.mp4").exists() and (nemo_dir / "video.en.vtt").exists()
    assert "Just keep swimming" in (nemo_dir / "video.en.vtt").read_text()
    assert (media / "thumbnails" / "videos" / f"{nemo.video_id}.jpg").read_bytes() == JPEG
    cartoon = videos.get(done["Old.Cartoon.webm"].video_id)
    assert (media / "uploads" / cartoon.video_id / "video.mp4").exists()
    assert done["Old.Cartoon.webm"].kind.startswith("transcode")
    assert repo.measured_speed(done["Old.Cartoon.webm"].kind)

    show = next(c for c in ChannelRepository(_conn(app)).list() if c.name == "Bluey")
    in_show = videos.list_ready(channel_id=show.id)
    assert [v.episode_key for v in in_show] == [1001, 1002]
    first = authed_client.get(f"/watch/{in_show[0].video_id}").text
    assert f'href="/watch/{in_show[1].video_id}"' in first and "Next episode" in first
    assert "Next episode" not in authed_client.get(f"/watch/{in_show[1].video_id}").text
    assert (drive / "Movies" / "Old.Cartoon.webm").exists(), "copy keeps the original"
    assert not list((media / "uploads" / ".tmp").glob("import-*"))

    page = authed_client.get("/parent/import/browse?root=0&path=Movies").text
    assert "already imported" in page


def test_move_removes_the_source_only_after_the_import(app, authed_client, drive):
    family = ChannelRepository(_conn(app)).create("Family")
    nemo = drive / "Movies" / "Finding.Nemo.2003.1080p.mp4"
    batch, jobs = _select(authed_client, app, files=["Movies/Finding.Nemo.2003.1080p.mp4"])
    j = jobs[0]
    authed_client.post(f"/parent/import/batch/{batch}", data={
        "action": "import", "mode": "move", f"j{j.id}_title": "Nemo",
        f"j{j.id}_channel_id": str(family)})
    _run_all(app)
    job = ImportJobRepository(_conn(app)).get(j.id)
    assert job.state == "done", job.error
    assert not nemo.exists()
    assert (drive / "Movies" / "poster.jpg").exists()
    assert (app.state.config.media_dir / "uploads" / job.video_id / "video.mp4").exists()


def test_same_file_twice_is_not_imported_twice(app, authed_client, drive):
    family = ChannelRepository(_conn(app)).create("Family")
    for _ in range(2):
        batch, jobs = _select(authed_client, app, files=["Movies/Finding.Nemo.2003.1080p.mp4"])
        authed_client.post(f"/parent/import/batch/{batch}", data={
            "action": "import", "mode": "copy", f"j{jobs[0].id}_title": "Nemo",
            f"j{jobs[0].id}_channel_id": str(family)})
        _run_all(app)
    assert VideoRepository(_conn(app)).count() == 1
    last = ImportJobRepository(_conn(app)).get(jobs[0].id)
    assert last.state == "done" and "Already in the library" in last.error


def test_queue_needs_a_channel_for_every_file(app, authed_client, drive):
    batch, jobs = _select(authed_client, app, files=["Movies/Old.Cartoon.webm"])
    resp = authed_client.post(f"/parent/import/batch/{batch}", data={
        "action": "import", f"j{jobs[0].id}_title": "Cartoon"}, follow_redirects=False)
    assert "Choose+a+channel" in resp.headers["location"] or \
        "Choose%20a%20channel" in resp.headers["location"]
    assert ImportJobRepository(_conn(app)).get(jobs[0].id).state == "draft"


def test_cancel_during_conversion_leaves_nothing_behind(app, authed_client, drive):
    family = ChannelRepository(_conn(app)).create("Family")
    big = _clip(drive / "Movies" / "Long.mkv", seconds=40, size="1920x1080")
    before = big.stat().st_size
    batch, jobs = _select(authed_client, app, files=["Movies/Long.mkv"])
    j = jobs[0]
    authed_client.post(f"/parent/import/batch/{batch}", data={
        "action": "import", "mode": "move", f"j{j.id}_title": "Long",
        f"j{j.id}_channel_id": str(family)})
    worker = build_worker(app.state.config, app.state.db_connect)
    app.state.import_worker = worker
    thread = threading.Thread(target=worker._run_next)
    thread.start()
    repo = ImportJobRepository(_conn(app))
    deadline = time.monotonic() + 60
    while repo.get(j.id).phase != "convert" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert repo.get(j.id).phase == "convert"
    work = app.state.config.media_dir / "uploads" / ".tmp" / work_dir_name(j.id)
    assert work.exists()
    status = authed_client.get("/parent/import/status.json").json()["jobs"][0]
    assert status["state"] == "working" and status["line"] == "Converting"

    authed_client.post(f"/parent/import/jobs/{j.id}/cancel")
    thread.join(timeout=30)
    assert not thread.is_alive()
    app.state.import_worker = None

    job = repo.get(j.id)
    assert job.state == "cancelled"
    assert not work.exists()
    assert big.exists() and big.stat().st_size == before
    assert VideoRepository(_conn(app)).count() == 0
    assert list((app.state.config.media_dir / "uploads").iterdir()) == [
        app.state.config.media_dir / "uploads" / ".tmp"]
    ps = subprocess.run(["pgrep", "-f", str(big)], capture_output=True, text=True)
    assert ps.stdout.strip() == ""


def test_restart_requeues_running_jobs_and_sweeps_leftovers(app, authed_client, drive):
    family = ChannelRepository(_conn(app)).create("Family")
    batch, jobs = _select(authed_client, app, files=["Movies/Old.Cartoon.webm"])
    authed_client.post(f"/parent/import/batch/{batch}", data={
        "action": "import", f"j{jobs[0].id}_title": "Cartoon",
        f"j{jobs[0].id}_channel_id": str(family)})
    repo = ImportJobRepository(_conn(app))
    assert repo.claim(jobs[0].id)
    tmp = app.state.config.media_dir / "uploads" / ".tmp"
    (tmp / work_dir_name(jobs[0].id)).mkdir(parents=True)
    (tmp / work_dir_name(jobs[0].id) / "video.mp4").write_bytes(b"half")
    (tmp / "import-999").mkdir()
    worker = build_worker(app.state.config, app.state.db_connect)
    repo.recover_after_restart()
    worker.sweep(_Shared(_conn(app)))
    assert repo.get(jobs[0].id).state == "queued"
    assert not (tmp / work_dir_name(jobs[0].id)).exists() and not (tmp / "import-999").exists()


class _FakeLookup:
    enabled = True

    def search(self, origin, title, year, link, language):
        self.asked = (origin, title, year)
        return [Candidate(source="wikipedia", title="Finding Nemo", year=2003, kind="Film",
                          rating="G", description="A clownfish looks for his son.",
                          image_url="https://upload.wikimedia.org/nemo.jpg", ref="Q132863",
                          url="https://en.wikipedia.org/wiki/Finding_Nemo")]

    def image(self, url):
        return JPEG


def test_lookup_offers_candidates_frames_and_saves_the_choice(app, authed_client, drive):
    fake = _FakeLookup()
    app.dependency_overrides[get_lookup_service] = lambda: fake
    try:
        batch, jobs = _select(authed_client, app, files=["Movies/Old.Cartoon.webm"])
        j = jobs[0]
        page = authed_client.get(f"/parent/import/jobs/{j.id}/lookup?origin=movie").text
        assert fake.asked == ("movie", "Old Cartoon", None)
        assert "Rated G" in page and "A clownfish looks for his son." in page
        assert f"/parent/import/jobs/{j.id}/preview/cand-0" in page
        assert page.count("frame-") >= 4
        assert authed_client.get(f"/parent/import/jobs/{j.id}/preview/cand-0").content == JPEG
        assert authed_client.get(
            f"/parent/import/jobs/{j.id}/preview/..%2F..%2Fconfig.json").status_code == 404

        authed_client.post(f"/parent/import/jobs/{j.id}/pick", data={
            "kind": "candidate", "image": "cand-0", "description": "Edited by a parent.",
            "ref": "Q132863", "url": "https://en.wikipedia.org/wiki/Finding_Nemo",
            "title": "Finding Nemo", "year": "2003", "use_title": "1"})
        job = ImportJobRepository(_conn(app)).get(j.id)
        assert (job.details["title"], job.details["year"]) == ("Finding Nemo", 2003)
        assert job.details["description"] == "Edited by a parent."
        assert job.details["origin_ref"] == "Q132863"
        assert authed_client.get(f"/parent/import/jobs/{j.id}/picture").content == JPEG

        authed_client.post(f"/parent/import/jobs/{j.id}/pick", data={"kind": "frame", "at": "0.1"})
        job = ImportJobRepository(_conn(app)).get(j.id)
        assert job.details["frame_at"] == 0.1 and not job.has_picture
    finally:
        app.dependency_overrides.pop(get_lookup_service, None)


def test_import_pages_need_the_parent(client, app, drive):
    for url in ("/parent/import", "/parent/import/status.json", "/parent/import/browse"):
        assert client.get(url, follow_redirects=False).status_code in (302, 303, 401, 403)


def test_settings_save_import_folders(app, authed_client, tmp_path):
    resp = authed_client.post("/parent/settings/imports", data={
        "import_roots": f"{tmp_path}\n{tmp_path}\n", "lookups_enabled": "",
        "tmdb_api_key": " abc "})
    assert resp.status_code == 200
    cfg = app.state.config
    assert cfg.storage.import_roots == [str(tmp_path)]
    assert cfg.imports.lookups_enabled is False and cfg.imports.tmdb_api_key == "abc"
    resp = authed_client.post("/parent/settings/imports", data={"import_roots": "relative/dir"})
    assert "full path" in resp.text and cfg.storage.import_roots == [str(tmp_path)]


def test_dashboard_shows_running_imports(app, authed_client, drive):
    family = ChannelRepository(_conn(app)).create("Family")
    batch, jobs = _select(authed_client, app, files=["Movies/Old.Cartoon.webm"])
    authed_client.post(f"/parent/import/batch/{batch}", data={
        "action": "import", f"j{jobs[0].id}_title": "Cartoon",
        f"j{jobs[0].id}_channel_id": str(family)})
    assert "1 import waiting or running" in authed_client.get("/parent/").text
    authed_client.post("/parent/import/cancel-all")
    assert ImportJobRepository(_conn(app)).get(jobs[0].id).state == "cancelled"
    assert "waiting or running" not in authed_client.get("/parent/").text


def test_apply_to_all_keeps_a_show_grouping(app, authed_client, drive):
    family = ChannelRepository(_conn(app)).create("Family")
    batch, jobs = _select(authed_client, app, whole=True)
    authed_client.post(f"/parent/import/batch/{batch}", data={"action": "show:Bluey"})
    authed_client.post(f"/parent/import/batch/{batch}", data={
        "action": "apply", "all_channel_id": str(family)})
    by_name = {j.name: j.details for j in ImportJobRepository(_conn(app)).list_batch(batch)}
    assert by_name["Bluey.S01E01.mp4"]["new_channel"] == "Bluey"
    assert by_name["Bluey.S01E01.mp4"]["channel_id"] is None
    assert by_name["Old.Cartoon.webm"]["channel_id"] == family
