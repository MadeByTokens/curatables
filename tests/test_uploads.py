"""Tests for Phase 2 parent uploads.

Covers MediaProbeService parsing and validation, UploadService
lifecycle (create/append/finalize/dedup/sweep/unsupported-codec),
StorageService.video_file_path mode-aware routing, the tus.io
protocol routes end-to-end via TestClient, and the new
max_upload_gb advanced settings field.
"""

import base64
import hashlib
import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from app.models import Video
from app.repositories import VideoRepository, ChannelRepository
from app.services.media_probe import (
    MediaProbeService, UnsupportedCodec, ProbeError, ProbeResult,
)
from app.services.storage import StorageService
from app.services.uploads import (
    UploadService, UploadNotFound, UploadOffsetMismatch, UploadError,
)


_GB = 1_073_741_824


_SAMPLE_DECODERS_OUTPUT = """Decoders:
 V..... = Video
 A..... = Audio
 S..... = Subtitle
 .F.... = Frame-level multithreading
 ..S... = Slice-level multithreading
 ...X.. = Codec is experimental
 ....B. = Supports draw_horiz_band
 .....D = Supports direct rendering method 1
 ------
 V....D 012v                 Uncompressed 4:2:2 10-bit
 V....D h264                 H.264 / AVC / MPEG-4 AVC
 V....D hevc                 H.265 / HEVC
 VF...D vp9                  Google VP9
 A....D aac                  AAC (Advanced Audio Coding)
 S..... srt                  SubRip subtitle
"""


def _fake_run(stdout: str, returncode: int = 0):
    """Helper to build a subprocess.run return value."""
    cp = MagicMock()
    cp.stdout = stdout
    cp.stderr = ""
    cp.returncode = returncode
    return cp


# ---------------------------------------------------------------------------
# MediaProbeService
# ---------------------------------------------------------------------------

class TestMediaProbe:
    def test_decoder_parsing_skips_legend_rows(self):
        svc = MediaProbeService()
        with patch("app.services.media_probe.subprocess.run",
                   return_value=_fake_run(_SAMPLE_DECODERS_OUTPUT)):
            decoders = svc.get_supported_video_decoders()
        assert "h264" in decoders
        assert "hevc" in decoders
        assert "vp9" in decoders
        assert "012v" in decoders
        assert "=" not in decoders
        assert "aac" not in decoders  # audio decoder, not video
        assert "srt" not in decoders  # subtitle decoder, not video

    def test_decoder_cache_is_one_shot(self):
        svc = MediaProbeService()
        with patch("app.services.media_probe.subprocess.run",
                   return_value=_fake_run(_SAMPLE_DECODERS_OUTPUT)) as m:
            svc.get_supported_video_decoders()
            svc.get_supported_video_decoders()
        assert m.call_count == 1

    def test_probe_parses_ffprobe_json(self, tmp_path):
        svc = MediaProbeService()
        fake_json = json.dumps({
            "streams": [
                {
                    "codec_name": "h264",
                    "codec_type": "video",
                    "width": 1920,
                    "height": 1080,
                    "duration": "12.34",
                    "r_frame_rate": "30000/1001",
                },
                {
                    "codec_name": "aac",
                    "codec_type": "audio",
                },
            ],
            "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "duration": "12.34"},
        })
        f = tmp_path / "fake.mp4"
        f.write_bytes(b"not actually a video")
        with patch("app.services.media_probe.subprocess.run",
                   return_value=_fake_run(fake_json)):
            result = svc.probe(f)
        assert result.codec_name == "h264"
        assert result.width == 1920
        assert result.height == 1080
        assert result.duration_seconds == pytest.approx(12.34)
        assert result.container == "mov"
        assert result.audio_codec == "aac"
        assert result.fps == pytest.approx(29.97, abs=0.01)

    def test_probe_raises_on_nonzero_exit(self, tmp_path):
        svc = MediaProbeService()
        f = tmp_path / "bad.mp4"
        f.write_bytes(b"broken")
        with patch("app.services.media_probe.subprocess.run",
                   return_value=_fake_run("", returncode=1)):
            with pytest.raises(ProbeError):
                svc.probe(f)

    def test_validate_accepts_supported_codec(self, tmp_path):
        svc = MediaProbeService()
        svc._video_decoders = {"h264", "hevc"}
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")
        with patch.object(svc, "probe", return_value=ProbeResult(
                codec_name="h264", width=1920, height=1080,
                duration_seconds=5.0, container="mov")):
            result = svc.validate(f, original_filename="video.mp4")
        assert result.codec_name == "h264"

    def test_validate_raises_unsupported_codec_with_hint(self, tmp_path):
        svc = MediaProbeService()
        svc._video_decoders = {"h264"}  # no hevc
        f = tmp_path / "clip.mkv"
        f.write_bytes(b"fake")
        with patch.object(svc, "probe", return_value=ProbeResult(
                codec_name="hevc", width=1920, height=1080,
                duration_seconds=5.0, container="matroska")):
            with pytest.raises(UnsupportedCodec) as exc:
                svc.validate(f, original_filename="clip.mkv")
        assert exc.value.codec_name == "hevc"
        assert "hevc" in exc.value.conversion_hint
        assert "libx264" in exc.value.conversion_hint
        assert "clip.mkv" in exc.value.conversion_hint


# ---------------------------------------------------------------------------
# UploadService lifecycle
# ---------------------------------------------------------------------------

def _make_services(tmp_path, db):
    (tmp_path / "uploads" / ".tmp").mkdir(parents=True, exist_ok=True)
    storage = StorageService(tmp_path, backend=None,
                             min_free_bytes=100 * _GB)  # never blocks in tests
    probe = MediaProbeService()
    probe._video_decoders = {"h264"}
    video_repo = VideoRepository(db)
    channel_repo = ChannelRepository(db)
    from app.services.thumbnails import ThumbnailService
    thumbnails = ThumbnailService(tmp_path)
    uploads = UploadService(storage, probe, video_repo, channel_repo, thumbnails)
    return uploads, video_repo, channel_repo


class TestUploadLifecycle:
    def test_create_and_offset(self, tmp_path, db):
        uploads, _, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        session = uploads.create("home.mp4", 200, cid, "Home video")
        assert session.filename == "home.mp4"
        assert uploads.get_offset(session.token) == 0
        assert (tmp_path / "uploads" / ".tmp" / session.token).exists()
        assert (tmp_path / "uploads" / ".tmp" / f"{session.token}.json").exists()

    def test_append_advances_offset(self, tmp_path, db):
        uploads, _, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        session = uploads.create("home.mp4", 10, cid, "")
        new_offset = uploads.append(session.token, 0, b"hello")
        assert new_offset == 5
        new_offset = uploads.append(session.token, 5, b"world")
        assert new_offset == 10

    def test_append_offset_mismatch_raises(self, tmp_path, db):
        uploads, _, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        session = uploads.create("home.mp4", 10, cid, "")
        uploads.append(session.token, 0, b"hello")
        with pytest.raises(UploadOffsetMismatch):
            uploads.append(session.token, 0, b"again")

    def test_cancel_removes_tmp_and_sidecar(self, tmp_path, db):
        uploads, _, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        session = uploads.create("home.mp4", 10, cid, "")
        uploads.cancel(session.token)
        assert not (tmp_path / "uploads" / ".tmp" / session.token).exists()
        assert not (tmp_path / "uploads" / ".tmp" / f"{session.token}.json").exists()

    def test_finalize_happy_path(self, tmp_path, db):
        uploads, video_repo, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        content = b"a" * 100
        session = uploads.create("home_video.mp4", len(content), cid, "")
        uploads.append(session.token, 0, content)

        with patch.object(uploads.probe, "validate", return_value=ProbeResult(
                codec_name="h264", width=640, height=480,
                duration_seconds=3.0, container="mov")):
            with patch.object(uploads.thumbnails, "extract_frame", return_value=None):
                video = uploads.finalize(session.token)

        expected_id = f"up_{hashlib.sha256(content).hexdigest()[:16]}"
        assert video.video_id == expected_id
        assert video.storage_mode == "uploaded"
        assert video.download_status == "ready"
        assert video.channel_id == cid
        assert video.source_id is None
        assert video.duration == 3
        assert video.resolution == "480p"
        # File actually moved into uploads/<id>/video.mp4
        target = tmp_path / "uploads" / expected_id / "video.mp4"
        assert target.exists()
        assert target.read_bytes() == content
        # Tmp and sidecar are gone
        assert not (tmp_path / "uploads" / ".tmp" / session.token).exists()
        assert not (tmp_path / "uploads" / ".tmp" / f"{session.token}.json").exists()
        # Title derived from filename
        assert video.title == "home video"

    def test_finalize_dedup_returns_existing_video(self, tmp_path, db):
        uploads, video_repo, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        content = b"b" * 50

        def _upload_once(title):
            s = uploads.create("clip.mp4", len(content), cid, title)
            uploads.append(s.token, 0, content)
            with patch.object(uploads.probe, "validate", return_value=ProbeResult(
                    codec_name="h264", width=320, height=240,
                    duration_seconds=1.0, container="mov")):
                with patch.object(uploads.thumbnails, "extract_frame", return_value=None):
                    return uploads.finalize(s.token)

        first = _upload_once("First title")
        second = _upload_once("Second title")
        assert first.video_id == second.video_id
        # Only one row in videos
        assert video_repo.count() == 1
        # The second finalize cleaned up its tmp file
        tmp_dir = tmp_path / "uploads" / ".tmp"
        assert list(tmp_dir.iterdir()) == []

    def test_finalize_unsupported_codec_cleans_up(self, tmp_path, db):
        uploads, video_repo, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        content = b"c" * 30
        session = uploads.create("av1.mp4", len(content), cid, "")
        uploads.append(session.token, 0, content)

        hint = "conversion hint with ffmpeg command"
        with patch.object(uploads.probe, "validate",
                          side_effect=UnsupportedCodec(hint, "av1", hint)):
            with pytest.raises(UnsupportedCodec):
                uploads.finalize(session.token)

        assert video_repo.count() == 0
        assert not (tmp_path / "uploads" / ".tmp" / session.token).exists()
        assert not (tmp_path / "uploads" / ".tmp" / f"{session.token}.json").exists()

    def test_finalize_incomplete_upload_raises(self, tmp_path, db):
        uploads, video_repo, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        session = uploads.create("short.mp4", 100, cid, "")
        uploads.append(session.token, 0, b"only 50 bytes of content here____________________")
        with pytest.raises(UploadError):
            uploads.finalize(session.token)
        assert video_repo.count() == 0

    def test_sweep_abandoned_removes_old_tmp(self, tmp_path, db):
        uploads, _, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        session = uploads.create("abandoned.mp4", 10, cid, "")
        # Backdate the tmp file's mtime by 25 hours
        path = tmp_path / "uploads" / ".tmp" / session.token
        sidecar = tmp_path / "uploads" / ".tmp" / f"{session.token}.json"
        old = time.time() - 25 * 3600
        os.utime(path, (old, old))
        os.utime(sidecar, (old, old))
        removed = uploads.sweep_abandoned(ttl_hours=24)
        assert removed == 2
        assert not path.exists()
        assert not sidecar.exists()

    def test_sweep_leaves_fresh_uploads(self, tmp_path, db):
        uploads, _, channel_repo = _make_services(tmp_path, db)
        cid = channel_repo.create("Family")
        session = uploads.create("fresh.mp4", 10, cid, "")
        removed = uploads.sweep_abandoned(ttl_hours=24)
        assert removed == 0
        assert (tmp_path / "uploads" / ".tmp" / session.token).exists()


# ---------------------------------------------------------------------------
# StorageService.video_file_path mode branching
# ---------------------------------------------------------------------------

class TestVideoFilePath:
    def test_legacy_cache_mode_returns_videos_tree_path(self, tmp_path):
        storage = StorageService(tmp_path, backend=None, min_free_bytes=0)
        video = Video(video_id="abc12345678", title="t", original_title="t",
                      storage_mode="cache")
        path = storage.video_file_path(video)
        assert path == tmp_path / "videos" / "abc12345678" / "video.mp4"

    def test_uploaded_mode_globs_uploads_tree(self, tmp_path):
        storage = StorageService(tmp_path, backend=None, min_free_bytes=0)
        vdir = tmp_path / "uploads" / "up_deadbeef12345678"
        vdir.mkdir(parents=True)
        (vdir / "video.mkv").write_bytes(b"x")
        video = Video(video_id="up_deadbeef12345678", title="t", original_title="t",
                      storage_mode="uploaded")
        path = storage.video_file_path(video)
        assert path.exists()
        assert path.suffix == ".mkv"

    def test_uploaded_mode_missing_returns_nonexistent_path(self, tmp_path):
        storage = StorageService(tmp_path, backend=None, min_free_bytes=0)
        video = Video(video_id="up_missing00000000", title="t", original_title="t",
                      storage_mode="uploaded")
        path = storage.video_file_path(video)
        assert not path.exists()


# ---------------------------------------------------------------------------
# tus.io HTTP routes
# ---------------------------------------------------------------------------

def _encode_metadata(pairs: dict[str, str]) -> str:
    parts = []
    for k, v in pairs.items():
        b64 = base64.b64encode(v.encode("utf-8")).decode("ascii")
        parts.append(f"{k} {b64}")
    return ",".join(parts)


def _get_channel_id(app) -> int:
    """Create a channel in the test DB and return its id."""
    from app.dependencies import get_db
    override = app.dependency_overrides[get_db]
    conn = next(override())
    repo = ChannelRepository(conn)
    return repo.create("Family")


class TestTusRoutes:
    def test_options_advertises_tus_capabilities(self, authed_client):
        resp = authed_client.options("/parent/upload/tus")
        assert resp.status_code == 204
        assert resp.headers.get("tus-version") == "1.0.0"
        assert "creation" in resp.headers.get("tus-extension", "")
        assert resp.headers.get("tus-resumable") == "1.0.0"

    def test_create_rejects_wrong_resumable_version(self, authed_client, app):
        cid = _get_channel_id(app)
        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": "100",
            "Tus-Resumable": "0.2.2",
            "Upload-Metadata": _encode_metadata({
                "filename": "x.mp4", "channel_id": str(cid),
            }),
        })
        assert resp.status_code == 412

    def test_create_rejects_missing_channel_id(self, authed_client):
        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": "100",
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({"filename": "x.mp4"}),
        })
        assert resp.status_code == 400

    def test_full_upload_flow(self, authed_client, app):
        cid = _get_channel_id(app)
        content = b"Z" * 200

        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": str(len(content)),
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "big_clip.mp4", "channel_id": str(cid), "title": "Big clip",
            }),
        })
        assert resp.status_code == 201
        location = resp.headers["location"]
        assert location.startswith("/parent/upload/tus/")
        token = location.rsplit("/", 1)[1]

        resp = authed_client.head(location)
        assert resp.status_code == 200
        assert resp.headers["upload-offset"] == "0"
        assert resp.headers["upload-length"] == str(len(content))

        resp = authed_client.patch(location, content=content[:100], headers={
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
            "Tus-Resumable": "1.0.0",
        })
        assert resp.status_code == 204
        assert resp.headers["upload-offset"] == "100"

        resp = authed_client.patch(location, content=content[100:], headers={
            "Upload-Offset": "100",
            "Content-Type": "application/offset+octet-stream",
            "Tus-Resumable": "1.0.0",
        })
        assert resp.status_code == 204
        assert resp.headers["upload-offset"] == "200"

        # Finalize via patched probe
        with patch("app.services.uploads.MediaProbeService.validate",
                   return_value=ProbeResult(codec_name="h264", width=640,
                                            height=480, duration_seconds=2.0,
                                            container="mov")):
            with patch("app.services.uploads.ThumbnailService.extract_frame",
                       return_value=None):
                resp = authed_client.post(f"/parent/upload/finalize/{token}")
        assert resp.status_code == 302
        location = resp.headers["location"]
        assert location.startswith("/parent/content")

        # Verify the video row exists with upload mode
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        expected_id = f"up_{hashlib.sha256(content).hexdigest()[:16]}"
        row = conn.execute(
            "SELECT video_id, storage_mode, download_status, channel_id "
            "FROM videos WHERE video_id = ?",
            (expected_id,),
        ).fetchone()
        assert row is not None
        assert row["storage_mode"] == "uploaded"
        assert row["download_status"] == "ready"
        assert row["channel_id"] == cid

    def test_resume_after_partial_upload(self, authed_client, app):
        cid = _get_channel_id(app)
        content = b"Q" * 400

        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": str(len(content)),
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "resume.mp4", "channel_id": str(cid),
            }),
        })
        location = resp.headers["location"]

        # Upload first half, then simulate the client coming back to resume
        authed_client.patch(location, content=content[:200], headers={
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
            "Tus-Resumable": "1.0.0",
        })

        # Client reconnects, queries offset
        resp = authed_client.head(location)
        assert resp.status_code == 200
        assert resp.headers["upload-offset"] == "200"

        # Send the rest
        resp = authed_client.patch(location, content=content[200:], headers={
            "Upload-Offset": "200",
            "Content-Type": "application/offset+octet-stream",
            "Tus-Resumable": "1.0.0",
        })
        assert resp.status_code == 204
        assert resp.headers["upload-offset"] == "400"

    def test_patch_with_wrong_offset_returns_409(self, authed_client, app):
        cid = _get_channel_id(app)
        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": "100",
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "x.mp4", "channel_id": str(cid),
            }),
        })
        location = resp.headers["location"]
        resp = authed_client.patch(location, content=b"x" * 10, headers={
            "Upload-Offset": "50",
            "Content-Type": "application/offset+octet-stream",
            "Tus-Resumable": "1.0.0",
        })
        assert resp.status_code == 409

    def test_create_rejects_when_disk_full(self, authed_client, app):
        cid = _get_channel_id(app)
        with patch("app.services.storage.StorageService.check_can_write",
                   return_value=(False, "Not enough space")):
            resp = authed_client.post("/parent/upload/tus", headers={
                "Upload-Length": "100",
                "Tus-Resumable": "1.0.0",
                "Upload-Metadata": _encode_metadata({
                    "filename": "x.mp4", "channel_id": str(cid),
                }),
            })
        assert resp.status_code == 507
        assert "Not enough space" in resp.text

    def test_create_rejects_exceeds_max_upload(self, authed_client, app):
        cid = _get_channel_id(app)
        app.state.config.storage.max_upload_bytes = 1000
        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": "2000",
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "big.mp4", "channel_id": str(cid),
            }),
        })
        assert resp.status_code == 413

    def test_delete_terminates_upload(self, authed_client, app):
        cid = _get_channel_id(app)
        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": "100",
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "x.mp4", "channel_id": str(cid),
            }),
        })
        location = resp.headers["location"]
        resp = authed_client.delete(location)
        assert resp.status_code == 204
        # HEAD should now 404
        resp = authed_client.head(location)
        assert resp.status_code == 404

    def test_unauthenticated_upload_create_redirects(self, client, app):
        from app.services.auth import AuthService
        AuthService(app.state.config).set_password("tp")
        resp = client.post("/parent/upload/tus", headers={
            "Upload-Length": "100",
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "x.mp4", "channel_id": "1",
            }),
        })
        assert resp.status_code == 302
        assert "/parent/login" in resp.headers["location"]

    def test_upload_page_auto_creates_family_channel(self, authed_client, app):
        from app.dependencies import get_db
        conn = next(app.dependency_overrides[get_db]())
        repo = ChannelRepository(conn)
        assert repo.list() == []
        resp = authed_client.get("/parent/upload")
        assert resp.status_code == 200
        channels = repo.list()
        assert len(channels) == 1
        assert channels[0].name == "Family"

    def test_finalize_reports_unsupported_codec(self, authed_client, app):
        cid = _get_channel_id(app)
        content = b"!" * 80
        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": str(len(content)),
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "av1.mkv", "channel_id": str(cid),
            }),
        })
        location = resp.headers["location"]
        token = location.rsplit("/", 1)[1]
        authed_client.patch(location, content=content, headers={
            "Upload-Offset": "0",
            "Content-Type": "application/offset+octet-stream",
            "Tus-Resumable": "1.0.0",
        })
        hint = "This video uses the 'av1' codec"
        with patch("app.services.uploads.MediaProbeService.validate",
                   side_effect=UnsupportedCodec(hint, "av1", hint)):
            resp = authed_client.post(f"/parent/upload/finalize/{token}")
        assert resp.status_code == 415
        assert b"av1" in resp.content


# ---------------------------------------------------------------------------
# Advanced settings max_upload_gb
# ---------------------------------------------------------------------------

class TestSettingsMaxUpload:
    def test_max_upload_gb_round_trip(self, authed_client, app):
        resp = authed_client.post("/parent/settings/advanced", data={
            "port": 8080, "host": "0.0.0.0", "default_mode": "cache",
            "min_free_disk_gb": "2.0",
            "max_upload_gb": "5.5",
            "impersonate": "chrome", "cookies_file": "",
            "cookies_from_browser": "",
            "session_timeout_hours": 24, "log_level": "info",
        })
        assert resp.status_code == 200
        assert app.state.config.storage.max_upload_bytes == int(5.5 * _GB)

    def test_max_upload_gb_negative_rejected(self, authed_client, app):
        original = app.state.config.storage.max_upload_bytes
        resp = authed_client.post("/parent/settings/advanced", data={
            "port": 8080, "host": "0.0.0.0", "default_mode": "cache",
            "min_free_disk_gb": "2.0",
            "max_upload_gb": "-2",
            "impersonate": "chrome", "cookies_file": "",
            "cookies_from_browser": "",
            "session_timeout_hours": 24, "log_level": "info",
        })
        assert resp.status_code == 200
        assert b"cannot be negative" in resp.content
        assert app.state.config.storage.max_upload_bytes == original


# ---------------------------------------------------------------------------
# Upload page polish (tus-js-client best-practices fixes)
# ---------------------------------------------------------------------------

class TestUploadPagePolish:
    """Structural regressions for the upload.html fixes from
    docs/tus-js-client-best-practices.md. These are grep-the-HTML
    assertions that catch accidental deletion of critical client-side
    structures — they do not verify JS runtime behavior, which requires
    manual testing per the doc's section 9.
    """

    def test_page_contains_build_metadata_helper(self, authed_client):
        resp = authed_client.get("/parent/upload")
        assert resp.status_code == 200
        # The must-fix: a buildMetadata helper that filters empty values
        # so Safari doesn't reject the Upload-Metadata header.
        assert b"buildMetadata" in resp.content
        # And it must only conditionally set filetype/title.
        assert b"if (file.type)" in resp.content
        assert b"if (title)" in resp.content

    def test_page_contains_on_should_retry_with_unrecoverable_codes(self, authed_client):
        resp = authed_client.get("/parent/upload")
        assert resp.status_code == 200
        assert b"onShouldRetry" in resp.content
        # The five unrecoverable status codes must be referenced inline
        # so the retry short-circuit works for each one.
        for code in (b"507", b"413", b"401", b"403", b"410"):
            assert code in resp.content, f"Missing status code {code!r}"

    def test_page_contains_remove_fingerprint(self, authed_client):
        resp = authed_client.get("/parent/upload")
        assert resp.status_code == 200
        assert b"removeFingerprintOnSuccess" in resp.content

    def test_page_contains_find_previous_uploads(self, authed_client):
        resp = authed_client.get("/parent/upload")
        assert resp.status_code == 200
        assert b"findPreviousUploads" in resp.content
        # And the resume banner markup the promise callback populates.
        assert b"resume-banner" in resp.content

    def test_page_contains_beforeunload_guard(self, authed_client):
        resp = authed_client.get("/parent/upload")
        assert resp.status_code == 200
        assert b"beforeunload" in resp.content

    def test_page_contains_ios_preparing_state(self, authed_client):
        resp = authed_client.get("/parent/upload")
        assert resp.status_code == 200
        # The preparing state must show on the file-pick change event.
        assert b"Preparing" in resp.content
        # And the file-pick change listener must be wired up.
        assert b'fileInput.addEventListener("change"' in resp.content


class TestTusErrorContentType:
    """Assert the tus error responses set Content-Type: text/plain so
    browsers render the body consistently and tus-js-client's
    err.originalResponse.getBody() surfaces a readable message.
    """

    def test_413_response_is_plain_text(self, authed_client, app):
        # Pick a channel for the metadata so the 413 check runs.
        cid = _get_channel_id(app)
        # Force the upload length above the configured cap.
        app.state.config.storage.max_upload_bytes = 1000
        resp = authed_client.post("/parent/upload/tus", headers={
            "Upload-Length": "5000",
            "Tus-Resumable": "1.0.0",
            "Upload-Metadata": _encode_metadata({
                "filename": "big.mp4", "channel_id": str(cid),
            }),
        })
        assert resp.status_code == 413
        content_type = resp.headers.get("content-type", "")
        assert "text/plain" in content_type
        # Body must name the limit.
        assert b"1000" in resp.content or b"Upload exceeds" in resp.content

    def test_507_response_is_plain_text(self, authed_client, app):
        cid = _get_channel_id(app)
        with patch("app.services.storage.StorageService.check_can_write",
                   return_value=(False, "Only 0.1 GB free, need 2.0 GB")):
            resp = authed_client.post("/parent/upload/tus", headers={
                "Upload-Length": "100",
                "Tus-Resumable": "1.0.0",
                "Upload-Metadata": _encode_metadata({
                    "filename": "x.mp4", "channel_id": str(cid),
                }),
            })
        assert resp.status_code == 507
        content_type = resp.headers.get("content-type", "")
        assert "text/plain" in content_type
        assert b"0.1 GB free" in resp.content
