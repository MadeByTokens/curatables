"""scripts/backup.sh: a snapshot on the SD card, plus an optional copy on
the videos drive."""

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "backup.sh"


def _data(tmp_path, storage):
    data = tmp_path / "data"
    (data / "db").mkdir(parents=True)
    sqlite3.connect(data / "db" / "curatables.db").execute("CREATE TABLE t (x)").connection.close()
    (data / "config.json").write_text(json.dumps({"storage": storage}))
    return data


def _run(data, out):
    env = dict(os.environ, CURATABLES_DATA_DIR=str(data), PYTHON=sys.executable, RETAIN="2")
    return subprocess.run(["bash", str(SCRIPT), str(out)], env=env, capture_output=True, text=True, timeout=60)


def test_snapshot_goes_to_the_sd_card_only_by_default(tmp_path):
    media = tmp_path / "drive" / "curatables-library"
    media.mkdir(parents=True)
    data = _data(tmp_path, {"media_path": str(media)})
    proc = _run(data, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    assert len(list((tmp_path / "out").glob("curatables-*.db"))) == 1
    assert not (media / "backups").exists()


def test_snapshot_is_copied_to_the_videos_drive_when_asked(tmp_path):
    media = tmp_path / "drive" / "curatables-library"
    media.mkdir(parents=True)
    data = _data(tmp_path, {"media_path": str(media), "drive_backups": True})
    proc = _run(data, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    assert "Copied to the videos drive" in proc.stdout
    assert len(list((media / "backups").glob("curatables-*.db"))) == 1
    assert len(list((media / "backups").glob("curatables-*.config.json"))) == 1


def test_missing_drive_does_not_fail_the_backup(tmp_path):
    data = _data(tmp_path, {"media_path": str(tmp_path / "gone"), "drive_backups": True})
    proc = _run(data, tmp_path / "out")
    assert proc.returncode == 0, proc.stderr
    assert len(list((tmp_path / "out").glob("curatables-*.db"))) == 1
