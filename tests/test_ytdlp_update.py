"""The yt-dlp update that checks before it switches (scripts/ytdlp_update.py,
run as root on the box) and the check it relies on (scripts/ytdlp-check.py).

The root script is driven here with a fake box: its commands (pip,
systemctl, the checks) are replaced, and what it decided is read from
the result it writes for the page.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ytdlp_update", ROOT / "scripts" / "ytdlp_update.py")
up = importlib.util.module_from_spec(spec)
spec.loader.exec_module(up)

PASS = {"ok": True, "version": "", "checks": [{"name": "download", "ok": True}]}
FAIL_DOWNLOAD = {"ok": False, "version": "", "checks": [{"name": "details", "ok": True},
                                                        {"name": "download", "ok": False}]}


class Box:
    """What the update did, and what the box answers."""

    def __init__(self, tmp_path, monkeypatch, *, current="2026.3.17", newest="2026.8.19",
                 new_check=PASS, current_check=PASS, comes_back=True):
        self.versions = {"yt-dlp": current, "yt-dlp-ejs": "0.8.0"}
        self.newest = newest
        self.data = {"update-request.json": json.dumps({"kind": "yt-dlp"})}
        self.done = []
        self.comes_back = comes_back
        monkeypatch.setattr(up, "STATE_DIR", tmp_path / "state")
        monkeypatch.setattr(up, "read_data", lambda name: self.data.get(name))
        monkeypatch.setattr(up, "write_data", lambda name, payload: self.data.__setitem__(name, payload))
        monkeypatch.setattr(up, "remove_data", lambda name: self.data.pop(name, None))
        monkeypatch.setattr(up, "installed", lambda: dict(self.versions))
        monkeypatch.setattr(up, "fresh_dir", self._fresh)
        monkeypatch.setattr(up, "pip", self._pip)
        monkeypatch.setattr(up, "check", lambda site=None, offline=False:
                            new_check if site is not None else current_check)
        monkeypatch.setattr(up, "wait_for_downloads", lambda: self.done.append("waited"))
        monkeypatch.setattr(up, "run", self._run)
        monkeypatch.setattr(up, "settled", lambda target: self.comes_back and up.same(self.versions, target))
        monkeypatch.setattr(up, "restart", lambda: self.done.append("restart"))

    @staticmethod
    def _fresh(path):
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _pip(self, args, timeout=900):
        if args[0] == "download":
            dest = Path(args[args.index("-d") + 1])
            pins = [a for a in args if "==" in a]
            for name, version in ([p.split("==") for p in pins] if pins
                                  else [("yt-dlp", self.newest), ("yt-dlp-ejs", "0.8.0")]):
                (dest / f"{name.replace('-', '_')}-{version}-py3-none-any.whl").write_text("")
        elif args[0] == "install" and "--target" not in args:
            for wheel in args:
                if wheel.endswith(".whl"):
                    m = up._WHEEL.match(Path(wheel).name)
                    self.versions[m["name"].replace("_", "-")] = m["version"]
            self.done.append("installed " + self.versions["yt-dlp"])
        return subprocess.CompletedProcess(args, 0, "", "")

    def _run(self, argv, **kw):
        if argv[:1] == ["systemctl"]:
            self.done.append(" ".join(argv[1:3]))
        return subprocess.CompletedProcess(argv, 0, "", "")

    @property
    def result(self):
        return self.data["update-result.json"]


def test_a_new_version_that_passes_goes_in(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch)
    assert up.main() == 0
    assert box.result["status"] == "ok" and box.result["to_version"] == "2026.8.19"
    assert "downloads" in box.result["message"]
    assert box.done.index("waited") < box.done.index("installed 2026.8.19")
    assert "update-request.json" not in box.data and "update-progress.json" not in box.data
    assert not (tmp_path / "state" / up.NOTE).exists()


def test_a_new_version_that_cannot_download_is_not_used(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, new_check=FAIL_DOWNLOAD)
    up.main()
    assert box.result["status"] == "kept"
    assert box.result["message"].startswith("Kept 2026.3.17: the new 2026.8.19 could not download videos")
    assert not any(step.startswith("installed") or step.startswith("stop") for step in box.done)
    assert box.versions["yt-dlp"] == "2026.3.17"


def test_when_neither_version_works_it_says_to_try_later(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, new_check=FAIL_DOWNLOAD, current_check=FAIL_DOWNLOAD)
    up.main()
    assert box.result["status"] == "kept" and "try again later" in box.result["message"]
    assert box.versions["yt-dlp"] == "2026.3.17"


def test_a_new_version_that_fixes_a_broken_one_goes_in(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, current_check=FAIL_DOWNLOAD)
    up.main()
    assert box.result["status"] == "ok" and box.versions["yt-dlp"] == "2026.8.19"


def test_the_newest_already_in_use_changes_nothing(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, newest="2026.03.17")
    up.main()
    assert box.result["status"] == "ok" and box.result["message"] == "Already the newest version."
    assert box.done == []


def test_curatables_not_coming_back_puts_the_previous_version_back(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, comes_back=False)
    assert up.main() == 1
    assert box.done[-3:] == ["stop curatables.service", "installed 2026.3.17", "start curatables.service"]
    assert box.result["status"] == "error" and "2026.3.17 was put back" in box.result["message"]
    assert box.versions["yt-dlp"] == "2026.3.17"


def test_a_power_cut_during_the_switch_is_finished_at_boot(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, current="2026.8.19")
    (tmp_path / "state").mkdir()
    (tmp_path / "state" / up.NOTE).write_text(json.dumps({
        "before": {"yt-dlp": "2026.3.17"}, "target": {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}}))
    up.main()
    assert box.result["status"] == "ok" and "finished after the box restarted" in box.result["message"]
    assert not (tmp_path / "state" / up.NOTE).exists() and "update-request.json" not in box.data


def test_a_power_cut_before_the_new_version_was_in_goes_back(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch, current="2026.3.17")
    previous = tmp_path / "state" / "previous"
    previous.mkdir(parents=True)
    (previous / "yt_dlp-2026.3.17-py3-none-any.whl").write_text("")
    (tmp_path / "state" / up.NOTE).write_text(json.dumps({
        "before": {"yt-dlp": "2026.3.17"}, "target": {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}}))
    up.main()
    assert box.result["status"] == "error" and "interrupted" in box.result["message"]
    assert "installed 2026.3.17" in box.done


def test_an_unexpected_error_mid_switch_puts_the_previous_version_back(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch)
    monkeypatch.setattr(up, "settled", lambda target: (_ for _ in ()).throw(OSError("disk")))
    assert up.main() == 1
    assert box.done[-1] == "start curatables.service" and box.versions["yt-dlp"] == "2026.3.17"
    assert "stopped unexpectedly" in box.result["message"]


def test_only_a_yt_dlp_request_is_acted_on(tmp_path, monkeypatch):
    box = Box(tmp_path, monkeypatch)
    box.data["update-request.json"] = json.dumps({"kind": "rm -rf /"})
    up.main()
    assert box.result["status"] == "error" and box.done == []


def test_wheel_names_give_versions(tmp_path):
    (tmp_path / "yt_dlp-2026.8.19-py3-none-any.whl").write_text("")
    (tmp_path / "yt_dlp_ejs-0.8.0-py3-none-any.whl").write_text("")
    assert up.wheel_versions(tmp_path) == {"yt-dlp": "2026.8.19", "yt-dlp-ejs": "0.8.0"}
    assert up.same({"yt-dlp": "2026.03.17", "yt-dlp-ejs": "0.8.0"}, {"yt-dlp": "2026.3.17", "yt-dlp-ejs": "0.8.0"})


# --- the check ---------------------------------------------------------------------

def _check(*args, cwd=None):
    r = subprocess.run([sys.executable, "-I", str(ROOT / "scripts" / "ytdlp-check.py"), *args],
                       capture_output=True, text=True, timeout=120, cwd=cwd)
    return r.returncode, json.loads(r.stdout.strip().splitlines()[-1])


def test_the_check_loads_the_installed_yt_dlp_offline(tmp_path):
    _code, result = _check(str(tmp_path), "--offline")
    names = {c["name"]: c for c in result["checks"]}
    assert names["loads"]["ok"] and names["settings"]["ok"]
    assert result["version"]
    assert set(names) == {"loads", "solver", "settings"}


def test_the_check_fails_a_yt_dlp_that_does_not_load(tmp_path):
    site = tmp_path / "site"
    (site / "yt_dlp").mkdir(parents=True)
    (site / "yt_dlp" / "__init__.py").write_text("raise ImportError('broken release')\n")
    code, result = _check(str(tmp_path / "data"), "--site", str(site), "--offline", cwd=tmp_path)
    assert code == 1 and not result["ok"]
    assert "broken release" in json.dumps(result)


@pytest.mark.parametrize("busy", [0, 2])
def test_the_check_counts_running_downloads(tmp_path, busy):
    from app.db.connection import create_connection, get_db_path
    from app.db.schema import init_schema
    get_db_path(tmp_path).parent.mkdir(parents=True)
    conn = create_connection(get_db_path(tmp_path))
    init_schema(conn)
    for i in range(busy):
        conn.execute("INSERT INTO videos (video_id, title, original_title, download_status) "
                     "VALUES (?, 'x', 'x', 'downloading')", (f"v{i}",))
    conn.commit()
    conn.close()
    assert _check(str(tmp_path), "--busy") == (0, {"downloading": busy})
