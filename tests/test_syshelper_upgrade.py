"""syshelper.upgrade: updating Curatables from a GitHub release with
rollback, system package updates, and shutting down."""

import hashlib
import io
import json
import tarfile

import pytest

from syshelper import system, upgrade
from syshelper.errors import HelperError
from syshelper.settings import SETTINGS
from tests.test_syshelper import FakeJob, fake  # noqa: F401  (fixture)


def _tree(root, version):
    (root / "app").mkdir(parents=True)
    (root / "app" / "__init__.py").write_text(f'__version__ = "{version}"\n')
    (root / "scripts").mkdir()
    (root / "scripts" / "apply-system.sh").write_text("#!/bin/sh\n")
    (root / "requirements.txt").write_text("fastapi\n")
    (root / "run.py").write_text("print('ok')\n")


def _tarball(path, version, extra=None):
    with tarfile.open(path, "w:gz") as tar:
        for name, data in {
            "app/__init__.py": f'__version__ = "{version}"\n',
            "scripts/apply-system.sh": "#!/bin/sh\n",
            "requirements.txt": "fastapi\n",
            "run.py": "print('ok')\n",
        }.items():
            info = tarfile.TarInfo(f"curatables-{version}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data.encode()))
        for info in extra or []:
            tar.addfile(info, io.BytesIO(b"x" * info.size) if info.isfile() else None)
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def box(fake, tmp_path, monkeypatch):  # noqa: F811
    code = tmp_path / "opt" / "curatables"
    _tree(code, "0.5.0")
    monkeypatch.setattr(SETTINGS, "code_dir", code)
    monkeypatch.setattr(SETTINGS, "updates_dir", tmp_path / "updates")
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "curatables-2026-10-09-030000.db").write_text("db")
    monkeypatch.setattr(SETTINGS, "backup_dir", backups)
    restarts = []
    monkeypatch.setattr(system, "later", lambda argv, delay=1.5: restarts.append(argv))
    monkeypatch.setattr(upgrade.time, "sleep", lambda s: None)
    tarball = tmp_path / "release.tar.gz"
    digest = _tarball(tarball, "0.6.0")
    release = {"tag_name": "v0.6.0", "body": "Notes", "published_at": "2026-10-10T00:00:00Z",
               "assets": [{"name": "curatables-0.6.0.tar.gz", "browser_download_url": "https://x/t.tgz"},
                          {"name": "SHA256SUMS", "browser_download_url": "https://x/sums"}]}
    state = {"sums": f"{digest}  curatables-0.6.0.tar.gz\n", "healthy": True}

    def download(url, dest, *, max_bytes, timeout=60):
        dest.write_bytes(tarball.read_bytes())
        return hashlib.sha256(tarball.read_bytes()).hexdigest()
    monkeypatch.setattr(upgrade.fetch, "get_json", lambda url, **k: release)
    monkeypatch.setattr(upgrade.fetch, "get_text", lambda url, **k: state["sums"])
    monkeypatch.setattr(upgrade.fetch, "download", download)
    monkeypatch.setattr(upgrade, "_healthy", lambda version, seconds=180: state["healthy"])
    return {"code": code, "fake": fake, "restarts": restarts, "state": state, "release": release}


@pytest.mark.parametrize("candidate,installed,expected", [
    ("0.6.0", "0.5.0", True), ("0.5.10", "0.5.9", True), ("0.5.0", "0.5.0", False),
    ("0.4.9", "0.5.0", False), ("", "0.5.0", False),
])
def test_version_order(candidate, installed, expected):
    assert upgrade.newer(candidate, installed) is expected


def test_info_reports_an_available_update(box):
    info = upgrade.update_info({})
    assert info["installed"] == "0.5.0" and info["latest"] == "0.6.0" and info["available"]
    assert info["notes"] == "Notes"


def test_a_release_without_the_box_files_is_not_offered(box):
    box["release"]["assets"] = []
    assert not upgrade.update_info({})["available"]
    with pytest.raises(HelperError, match="no update file"):
        upgrade.curatables_update({})


def test_already_newest_is_refused(box):
    box["release"]["tag_name"] = "v0.5.0"
    with pytest.raises(HelperError, match="already the newest"):
        upgrade.curatables_update({})


def test_update_builds_beside_the_running_code_then_switches(box):
    result = upgrade.curatables_update({})(FakeJob())
    assert result == {"from": "0.5.0", "to": "0.6.0"}
    assert upgrade.version_of(box["code"]) == "0.6.0"
    assert upgrade.version_of(upgrade._previous_dir()) == "0.5.0"
    argvs = box["fake"].argvs()
    pip = next(i for i, a in enumerate(argvs) if a[0].endswith("/.venv/bin/pip"))
    backup = argvs.index(["systemctl", "start", "curatables-backup.service"])
    stop = argvs.index(["systemctl", "stop", "curatables.service"])
    assert pip < backup < stop, "build and back up before the old version stops"
    assert any(a[0].endswith("apply-system.sh") for a in argvs)
    assert box["restarts"] == [["systemctl", "restart", "curatables-helper.service"]]
    last = json.loads((SETTINGS.updates_dir / upgrade.RESULT).read_text())
    assert last["state"] == "done" and last["backup"] == "curatables-2026-10-09-030000.db"


def test_an_update_that_does_not_start_goes_back_and_restores(box):
    box["state"]["healthy"] = False
    with pytest.raises(HelperError, match="went back to 0.5.0"):
        upgrade.curatables_update({})(FakeJob())
    assert upgrade.version_of(box["code"]) == "0.5.0"
    assert upgrade.version_of(upgrade._failed_dir()) == "0.6.0"
    restore = [a for a in box["fake"].argvs() if a[:2] == ["runuser", "-u"]]
    assert restore and restore[0][-1].endswith("curatables-2026-10-09-030000.db")
    assert json.loads((SETTINGS.updates_dir / upgrade.RESULT).read_text())["state"] == "failed"


def test_a_checksum_mismatch_changes_nothing(box):
    box["state"]["sums"] = "0" * 64 + "  curatables-0.6.0.tar.gz\n"
    with pytest.raises(HelperError, match="checksum"):
        upgrade.curatables_update({})(FakeJob())
    assert upgrade.version_of(box["code"]) == "0.5.0"
    assert not box["fake"].ran("systemctl", "stop", "curatables.service")


def test_failed_package_install_changes_nothing(box):
    box["fake"].on([str(upgrade._new_dir() / ".venv" / "bin" / "pip")], rc=1)
    with pytest.raises(HelperError, match="Nothing was changed"):
        upgrade.curatables_update({})(FakeJob())
    assert upgrade.version_of(box["code"]) == "0.5.0" and not upgrade._new_dir().exists()


def test_rollback_swaps_back_and_restores_the_backup(box):
    upgrade.curatables_update({})(FakeJob())
    box["fake"].calls.clear()
    result = upgrade.curatables_rollback({})(FakeJob())
    assert result["to"] == "0.5.0" and upgrade.version_of(box["code"]) == "0.5.0"
    assert upgrade.version_of(upgrade._previous_dir()) == "0.6.0"
    assert any(a[:2] == ["runuser", "-u"] for a in box["fake"].argvs())


def test_no_previous_version_no_rollback(box):
    with pytest.raises(HelperError, match="no previous version"):
        upgrade.curatables_rollback({})


@pytest.mark.parametrize("name,kind", [
    ("curatables-0.6.0/../../etc/evil", tarfile.REGTYPE),
    ("curatables-0.6.0/link", tarfile.SYMTYPE),
    ("curatables-0.6.0/dev", tarfile.CHRTYPE),
    ("other-top/file", tarfile.REGTYPE),
])
def test_unsafe_release_files_are_refused(tmp_path, name, kind):
    info = tarfile.TarInfo(name)
    info.type = kind
    if kind == tarfile.SYMTYPE:
        info.linkname = "/etc/shadow"
    info.size = 1 if kind == tarfile.REGTYPE else 0
    path = tmp_path / "r.tar.gz"
    _tarball(path, "0.6.0", extra=[info])
    with pytest.raises(HelperError):
        upgrade.unpack(path, tmp_path / "out")


def test_checksum_list_parsing():
    sums = "a" * 64 + "  other.tar.gz\n" + "b" * 64 + " *curatables-1.0.0.tar.gz\n"
    assert upgrade._expected_sum(sums, "curatables-1.0.0.tar.gz") == "b" * 64
    with pytest.raises(HelperError):
        upgrade._expected_sum(sums, "missing.tar.gz")


def test_system_update_runs_apt_without_questions(fake):  # noqa: F811
    upgrade.system_update({})(FakeJob())
    argvs = fake.argvs()
    assert ["apt-get", "update"] in argvs
    full = next(a for a in argvs if "full-upgrade" in a)
    assert "-y" in full and "Dpkg::Options::=--force-confold" in full


def test_system_update_explains_a_failed_list_fetch(fake):  # noqa: F811
    fake.on(["apt-get", "update"], rc=100)
    with pytest.raises(HelperError, match="internet connection"):
        upgrade.system_update({})(FakeJob())


def test_shutdown_powers_off_after_replying(monkeypatch):
    calls = []
    monkeypatch.setattr(system, "later", lambda argv, delay=1.5: calls.append(argv))
    assert system.shutdown({}) == {"shutting_down": True}
    assert calls == [["systemctl", "poweroff"]]


def test_healthy_needs_the_new_version_to_answer(monkeypatch):
    class Resp:
        status = 200

        def __init__(self, version):
            self.version = version

        def read(self):
            return json.dumps({"version": self.version}).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    answers = iter(["0.5.0", "0.6.0"])
    monkeypatch.setattr(upgrade.urllib.request, "urlopen", lambda url, timeout=5: Resp(next(answers)))
    monkeypatch.setattr(upgrade.time, "sleep", lambda s: None)
    assert upgrade._healthy("0.6.0", seconds=60)


def test_fetch_prefers_ipv4():
    from syshelper import fetch
    fake_results = [(10, 1, 6, "", ("::1", 443, 0, 0)), (2, 1, 6, "", ("127.0.0.1", 443))]
    orig = fetch._real_getaddrinfo
    try:
        fetch._real_getaddrinfo = lambda *a, **k: fake_results
        assert fetch._ipv4_first("x", 443)[0][0] == 2
    finally:
        fetch._real_getaddrinfo = orig


def test_fetch_refuses_plain_http():
    from syshelper import fetch
    with pytest.raises(HelperError, match="HTTPS"):
        fetch.get_json("http://example.com/x")

