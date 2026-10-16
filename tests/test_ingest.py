"""The shared upload/import tail: hash with progress and cancel, commit
the finished files and the row in one short step."""
import os
import time

import pytest

from app.repositories import VideoRepository
from app.services.ffmpeg_runner import CancelToken, Cancelled
from app.services.ingest import IngestService, VideoDetails, upload_video_id
from app.services.media_probe import MediaProbeService, ProbeResult
from app.services.storage import StorageService
from app.services.thumbnails import ThumbnailService

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 64


def _ingest(tmp_path, db):
    storage = StorageService(tmp_path, backend=None)
    return IngestService(storage, MediaProbeService(), VideoRepository(db),
                         ThumbnailService(tmp_path))


def test_hash_reports_progress_and_can_be_cancelled(tmp_path, db):
    ingest = _ingest(tmp_path, db)
    big = tmp_path / "big.bin"
    big.write_bytes(os.urandom(20 * 1024 * 1024))
    seen = []
    digest = ingest.hash_file(big, on_progress=seen.append)
    assert len(digest) == 64
    assert seen[-1].phase == "hash" and seen[-1].fraction == 1.0
    assert len(seen) == 3

    token = CancelToken()

    def cancel_after_first(progress):
        token.cancel()

    with pytest.raises(Cancelled):
        ingest.hash_file(big, on_progress=cancel_after_first, cancel=token)


def test_commit_moves_files_sets_picture_and_clears_the_work_folder(tmp_path, db):
    ingest = _ingest(tmp_path, db)
    work = ingest.work_dir("import-7")
    video = work / "video.mp4"
    video.write_bytes(b"movie")
    (work / "video.en.vtt").write_text("WEBVTT\n")
    (work / "ffmpeg.log").write_text("noise")
    probe = ProbeResult(codec_name="h264", width=1280, height=720,
                        duration_seconds=5400.4, container="mov")
    vid = upload_video_id("ab" * 32)
    out = ingest.commit(work, video, vid, probe, VideoDetails(
        title="Finding Nemo", channel_id=None, description="A fish.",
        thumbnail_bytes=JPEG, original_url="https://example.org/nemo"))

    target = tmp_path / "uploads" / vid
    assert (target / "video.mp4").read_bytes() == b"movie"
    assert (target / "video.en.vtt").exists()
    assert not work.exists()
    assert ThumbnailService(tmp_path).get_path(vid).read_bytes() == JPEG
    assert (out.title, out.description, out.duration, out.resolution) == (
        "Finding Nemo", "A fish.", 5400, "720p")
    assert (out.storage_mode, out.thumbnail_type, out.file_size) == ("uploaded", "custom", 5)
    assert out.original_url == "https://example.org/nemo"


def test_sweep_removes_stale_work_folders_but_not_import_jobs(tmp_path):
    storage = StorageService(tmp_path, backend=None)
    tmp = storage.upload_tmp_dir()
    old = time.time() - 48 * 3600
    for name in ("tok.work", "import-3"):
        (tmp / name).mkdir()
        (tmp / name / "video.mkv").write_bytes(b"x")
        os.utime(tmp / name / "video.mkv", (old, old))
        os.utime(tmp / name, (old, old))
    assert storage.sweep_upload_tmp(ttl_hours=24) == 1
    assert not (tmp / "tok.work").exists()
    assert (tmp / "import-3").exists()
