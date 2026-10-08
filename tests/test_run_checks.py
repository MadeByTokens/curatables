import pytest

import run


def test_require_mount_refuses_plain_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("CURATABLES_REQUIRE_MOUNT", "1")
    with pytest.raises(SystemExit):
        run.check_data_mount(str(tmp_path))


def test_require_mount_accepts_mount_point(monkeypatch):
    monkeypatch.setenv("CURATABLES_REQUIRE_MOUNT", "1")
    run.check_data_mount("/")


def test_no_check_without_env(tmp_path, monkeypatch):
    monkeypatch.delenv("CURATABLES_REQUIRE_MOUNT", raising=False)
    run.check_data_mount(str(tmp_path))
