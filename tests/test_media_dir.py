"""Media on a separate drive: videos, uploads, thumbnails and channel
pictures live under storage.media_path while the database, config and
logs stay in the data dir."""
import json
import re
from pathlib import Path

import pytest

from app.config import Config, ensure_directories, load_config
from app.main import create_app
from app.repositories import VideoRepository
from app.services.relocation import RelocationError, RelocationService


def _write_config(data_dir: Path, media: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "config.json").write_text(json.dumps(
        {"storage": {"path": str(data_dir), "media_path": str(media)},
         "parent": {"password_hash": None}}))


def test_media_subdirs_go_to_the_media_drive(tmp_path):
    data, media = tmp_path / "sd", tmp_path / "drive" / "curatables-library"
    media.mkdir(parents=True)
    _write_config(data, media)
    config = load_config(str(data))
    assert config.media_dir == media
    ensure_directories(config)
    assert (data / "db").is_dir() and (data / "logs").is_dir()
    assert not (data / "videos").exists() and not (data / "uploads").exists()
    for sub in ("videos", "thumbnails", "uploads/.tmp"):
        assert (media / sub).is_dir()


def test_missing_media_drive_refuses_to_start(tmp_path):
    data, media = tmp_path / "sd", tmp_path / "unplugged" / "curatables-library"
    _write_config(data, media)
    with pytest.raises(RuntimeError, match="is the drive plugged in"):
        create_app(data_dir=str(data))
    assert not media.exists()


def test_default_media_dir_is_the_data_dir(tmp_path):
    config = Config()
    config.storage.path = str(tmp_path)
    assert config.media_dir == tmp_path


def test_app_serves_media_from_the_media_drive(tmp_path):
    data, media = tmp_path / "sd", tmp_path / "drive"
    media.mkdir(parents=True)
    _write_config(data, media)
    app = create_app(data_dir=str(data))
    from app.dependencies import get_storage_service, get_thumbnail_service
    thumbs = get_thumbnail_service(app.state.config)
    assert thumbs.get_path("v1").is_relative_to(media)
    svc = get_storage_service(app.state.config, backend=None, probe=None)
    assert svc.video_dir("v1").is_relative_to(media)
    assert svc.uploads_dir().is_relative_to(media)


def test_relocation_is_refused_with_a_media_drive(tmp_path, db):
    config = Config()
    config.storage.path = str(tmp_path / "sd")
    config.storage.media_path = str(tmp_path / "drive")
    with pytest.raises(RelocationError, match="separate media drive"):
        RelocationService(config, VideoRepository(db)).move(str(tmp_path / "elsewhere"))


MEDIA_JOIN = re.compile(r"data_dir\s*/\s*[\"'](videos|uploads|thumbnails|channels)[\"']")


def test_no_code_builds_media_paths_from_the_data_dir():
    root = Path(__file__).resolve().parents[1] / "app"
    offenders = [f"{path.relative_to(root)}:{n}"
                 for path in root.rglob("*.py")
                 for n, line in enumerate(path.read_text().splitlines(), 1)
                 if MEDIA_JOIN.search(line)]
    assert offenders == []
