"""Box, Drives: listing drives, starting drive tasks, the missing-drive
screens, the FAT32 file cap and drives opened for importing."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Config, ensure_directories
from app.services import media_fs
from app.services.import_scan import ImportRoots
from app.services.storage import StorageService
from tests.fake_helper import FakeHelper, job

GB = 1024 ** 3


def _part(**kw):
    base = {"path": "/dev/sda1", "label": "KIDS", "uuid": "AAAA-1111", "fstype": "exfat", "fs_name": "exFAT",
            "size": 64 * GB, "free": 40 * GB, "mountpoint": "", "role": "", "usable": True,
            "is_videos_drive": False, "warning": ""}
    base.update(kw)
    return base


def _state(parts=None, media=None, power=None):
    return {"disks": [{"path": "/dev/sda", "name": "Acme Thumb", "size": 64 * GB,
                       "partitions": parts if parts is not None else [_part()]}],
            "media": {"configured": False, "uuid": "", "fstype": "", "mounted": False, "present": False,
                      "device": "", "free": 0, "size": 0, "sd_copy_bytes": 0, **(media or {})},
            "power": power or {}, "import_mounts": []}


@pytest.fixture
def helper(app):
    fake = FakeHelper(drives_list=_state(), backups_list={"backups": []})
    app.state.system_helper = fake
    app.state.config.parent.system_setup_done = True
    return fake


def test_lists_a_plugged_in_drive_with_its_actions(authed_client, helper):
    page = authed_client.get("/parent/system/drives").text
    assert "Acme Thumb" in page and "exFAT" in page
    assert "Use for videos" in page and "Import from it" in page and "Format this drive" in page
    assert "videos are on the <strong>SD card</strong>" in page


def test_fat32_drive_shows_its_limit(authed_client, helper):
    helper.responses["drives_list"] = _state([_part(fstype="vfat", fs_name="FAT32",
                                                    warning="FAT32 cannot hold files over 4 GB")])
    assert "FAT32 cannot hold files over 4 GB" in authed_client.get("/parent/system/drives").text


def test_videos_drive_offers_eject_repair_and_moving_back(authed_client, helper):
    helper.responses["drives_list"] = _state(
        [_part(role="videos", is_videos_drive=True, mountpoint="/mnt/curatables-media")],
        media={"configured": True, "mounted": True, "present": True, "device": "/dev/sda1",
               "fstype": "exfat", "free": 10 * GB, "size": 64 * GB, "drive_name": "Acme Thumb",
               "sd_copy_bytes": 3 * GB})
    page = authed_client.get("/parent/system/drives").text
    assert "Eject safely" in page and "Move videos back to the SD card" in page
    assert "Delete the old copy" in page and "3.0 GB" in page
    assert "Format this drive" not in page


def test_missing_videos_drive_offers_reconnect(authed_client, helper):
    helper.responses["drives_list"] = _state([], media={"configured": True, "present": False})
    page = authed_client.get("/parent/system/drives").text
    assert "not plugged in" in page and "Reconnect" in page


def test_an_unopened_videos_drive_offers_only_repair(authed_client, helper):
    helper.responses["drives_list"] = _state([_part(is_videos_drive=True)],
                                             media={"configured": True, "present": True})
    page = authed_client.get("/parent/system/drives").text
    card = page[page.index("USB drives"):page.index("Backups")]
    assert "holds the videos (not opened" in card and "Check &amp; repair" in card
    assert "Use for videos" not in card and "Import from it" not in card and "Eject" not in card
    assert "Format this drive" not in card


def test_under_voltage_warning(authed_client, helper):
    helper.responses["drives_list"] = _state(power={"under_voltage_now": True})
    assert "not getting enough power" in authed_client.get("/parent/system/drives").text


@pytest.mark.parametrize("path,data,action,params", [
    ("/parent/system/drives/use", {"device": "/dev/sda1"}, "drive_use_for_videos", {"device": "/dev/sda1"}),
    ("/parent/system/drives/eject", {"device": "/dev/sda1"}, "drive_eject", {"device": "/dev/sda1"}),
    ("/parent/system/drives/repair", {"device": "/dev/sda1"}, "drive_repair", {"device": "/dev/sda1"}),
    ("/parent/system/drives/reconnect", {}, "drive_reconnect", {}),
    ("/parent/system/drives/back-to-sd", {}, "drive_back_to_sd", {}),
    ("/parent/system/drives/delete-sd-copy", {}, "drive_delete_sd_copy", {}),
    ("/parent/system/drives/format", {"disk": "/dev/sda", "fstype": "exfat", "label": " KIDS ", "confirm": "ERASE"},
     "drive_format", {"disk": "/dev/sda", "fstype": "exfat", "label": "KIDS", "confirm": "ERASE"}),
    ("/parent/system/backups/restore", {"where": "sd", "name": "curatables-2026-10-01-030000.db"},
     "backup_restore", {"where": "sd", "name": "curatables-2026-10-01-030000.db"}),
])
def test_drive_buttons_start_helper_tasks(authed_client, helper, path, data, action, params):
    helper.responses[action] = job("t1")
    resp = authed_client.post(path, data=data)
    assert resp.status_code == 303 and resp.headers["location"].startswith("/parent/system/jobs/t1")
    assert helper.params(action) == params


def test_opening_a_drive_for_import_returns_to_the_import_page(authed_client, helper):
    helper.responses["drive_open_for_import"] = job("t2")
    resp = authed_client.post("/parent/system/drives/open", data={"device": "/dev/sda1"})
    assert resp.headers["location"] == "/parent/system/jobs/t2?back=/parent/import"
    page = authed_client.get(resp.headers["location"]).text
    assert 'href="/parent/import" class="btn btn-secondary">Go to Import' in page


def test_refused_task_shows_the_reason_on_the_drives_page(authed_client, helper):
    from app.services.system_helper import HelperFailed
    helper.responses["drive_format"] = HelperFailed("Type ERASE to confirm that everything on the drive will be deleted.")
    resp = authed_client.post("/parent/system/drives/format", data={"disk": "/dev/sda", "confirm": "no"})
    assert resp.status_code == 400 and "Type ERASE" in resp.text


def test_drive_backups_setting_is_saved(authed_client, app, helper):
    app.state.config.storage.media_path = str(Path(app.state.config.storage.path) / "lib")
    Path(app.state.config.storage.media_path).mkdir()
    resp = authed_client.post("/parent/system/drives/backups", data={"drive_backups": "1"})
    assert "also kept on the videos drive" in resp.text
    assert app.state.config.storage.drive_backups


def test_backups_are_listed_with_restore_buttons(authed_client, helper):
    helper.responses["backups_list"] = {"backups": [
        {"where": "drive", "name": "curatables-2026-10-01-030000.db", "size": 1, "when": "2026-10-01 03:00"}]}
    page = authed_client.get("/parent/system/drives").text
    assert "2026-10-01 03:00" in page and "Videos drive" in page and "Restore" in page


# --- videos drive missing -----------------------------------------------------

@pytest.fixture
def missing_drive(app, tmp_path):
    app.state.config.storage.media_path = str(tmp_path / "unplugged" / "curatables-library")
    app.state.config.parent.system_setup_done = True
    return app


def test_kids_see_the_resting_screen(missing_drive):
    c = TestClient(missing_drive, follow_redirects=False)
    for path in ("/", "/profiles", "/watch/abc"):
        resp = c.get(path)
        assert resp.status_code == 503 and "The videos are resting." in resp.text


def test_parent_dashboard_still_works_and_says_why(missing_drive, authed_client):
    page = authed_client.get("/parent/")
    assert page.status_code == 200 and "videos drive is not connected" in page.text


def test_server_starts_without_the_drive(tmp_path):
    config = Config()
    config.storage.path = str(tmp_path / "sd")
    config.storage.media_path = str(tmp_path / "gone" / "curatables-library")
    ensure_directories(config)
    assert not (tmp_path / "gone").exists()
    assert (tmp_path / "sd" / "db").is_dir()


def test_nothing_is_stored_while_the_drive_is_missing(tmp_path):
    allowed, reason = StorageService(tmp_path / "gone").check_can_write(10)
    assert not allowed and "not connected" in reason


# --- FAT32 --------------------------------------------------------------------

MOUNTINFO = """\
22 1 179:2 / / rw,noatime - ext4 /dev/mmcblk0p2 rw
40 22 8:1 / /mnt/curatables\\040media rw,noatime - vfat /dev/sda1 rw,uid=999
41 22 8:17 / /mnt/other rw - exfat /dev/sdb1 rw
"""


def test_filesystem_type_uses_the_deepest_mount(tmp_path, monkeypatch):
    info = tmp_path / "mountinfo"
    info.write_text(MOUNTINFO)
    media_fs._cache.clear()
    monkeypatch.setattr(media_fs.os.path, "realpath", lambda p: str(p))
    assert media_fs.filesystem_type(Path("/mnt/curatables media/curatables-library"), str(info)) == "vfat"
    media_fs._cache.clear()
    assert media_fs.filesystem_type(Path("/mnt/other/x"), str(info)) == "exfat"
    media_fs._cache.clear()
    assert media_fs.filesystem_type(Path("/home/curatables"), str(info)) == "ext4"
    media_fs._cache.clear()


def test_fat32_caps_file_sizes(tmp_path, monkeypatch):
    monkeypatch.setattr(media_fs, "filesystem_type", lambda path, mountinfo="": "vfat")
    assert media_fs.capped(tmp_path, 10 * GB) == media_fs.FAT_FILE_LIMIT
    assert media_fs.capped(tmp_path, GB) == GB
    monkeypatch.setattr("app.services.storage.file_size_limit", media_fs.file_size_limit)
    allowed, reason = StorageService(tmp_path).check_can_write(5 * GB)
    assert not allowed and "FAT32" in reason and "5.0 GB" in reason


def test_no_cap_on_other_filesystems(tmp_path, monkeypatch):
    monkeypatch.setattr(media_fs, "filesystem_type", lambda path, mountinfo="": "exfat")
    assert media_fs.capped(tmp_path, 10 * GB) == 10 * GB


# --- opened drives are import folders -----------------------------------------

def test_mounted_drives_are_import_folders(tmp_path, monkeypatch):
    config = Config()
    config.storage.path = str(tmp_path / "sd")
    drives = tmp_path / "drives"
    (drives / "Movies").mkdir(parents=True)
    (drives / "stale").mkdir()
    config.storage.drive_mounts_dir = str(drives)
    monkeypatch.setattr("app.services.import_scan.os.path.ismount", lambda p: Path(p).name == "Movies")
    assert ImportRoots(config).roots() == [(drives / "Movies").resolve()]
def test_crash_recovery_leaves_videos_alone_while_the_drive_is_missing(tmp_path, db):
    from app.db.schema import recover_from_crash
    db.execute("INSERT INTO videos (video_id, title, original_title, download_status, storage_mode) "
               "VALUES ('v1', 't', 't', 'ready', 'cache')")
    db.commit()
    recover_from_crash(db, tmp_path / "gone")
    assert db.execute("SELECT download_status FROM videos WHERE video_id='v1'").fetchone()[0] == "ready"
