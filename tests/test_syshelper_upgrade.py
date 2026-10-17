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


def _tarball(path, version, extra=None, migrations=()):
    files = {
        "app/__init__.py": f'__version__ = "{version}"\n',
        "scripts/apply-system.sh": "#!/bin/sh\n",
        "requirements.txt": "fastapi\n",
        "requirements.lock": "fastapi==1.0 --hash=sha256:00\n",
        "run.py": "print('ok')\n",
    }
    files.update({f"app/db/migrations/{m}": "" for m in migrations})
    with tarfile.open(path, "w:gz") as tar:
        for name, data in files.items():
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
    monkeypatch.setattr(upgrade, "_release_dir", lambda v: tmp_path / "opt" / f"curatables-{v}-test")
    monkeypatch.setattr(upgrade.time, "sleep", lambda s: None)
    tarball = tmp_path / "release.tar.gz"
    digest = _tarball(tarball, "0.6.0")
    tarball_with_migration = tmp_path / "release-m.tar.gz"
    _tarball(tarball_with_migration, "0.6.0", migrations=("0011_new.py",))
    release = {"tag_name": "v0.6.0", "body": "Notes", "published_at": "2026-10-10T00:00:00Z",
               "assets": [{"name": "curatables-0.6.0.tar.gz", "browser_download_url": "https://x/t.tgz"},
                          {"name": "SHA256SUMS", "browser_download_url": "https://x/sums"}]}
    state = {"sums": f"{digest}  curatables-0.6.0.tar.gz\n", "healthy": True, "migration": False,
             "bad_sum": False}

    def download(url, dest, *, max_bytes, timeout=60):
        src = tarball_with_migration if state["migration"] else tarball
        dest.write_bytes(src.read_bytes())
        digest = hashlib.sha256(src.read_bytes()).hexdigest()
        state["sums"] = state["sums"] if state["bad_sum"] else f"{digest}  curatables-0.6.0.tar.gz\n"
        return digest
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
    assert box["code"].is_symlink() and upgrade.version_of(box["code"]) == "0.6.0"
    assert upgrade.version_of(upgrade._previous_dir()) == "0.5.0"
    assert upgrade._real(upgrade._previous_dir()).name == "curatables-0.5.0-original"
    argvs = box["fake"].argvs()
    pip = next(i for i, a in enumerate(argvs) if a[1:3] == ["-m", "pip"])
    assert argvs[pip][0] == str(upgrade._release_dir("0.6.0") / ".venv" / "bin" / "python")
    new = upgrade._release_dir("0.6.0")
    assert argvs[pip][-3:] == ["--require-hashes", "-r", str(new / "requirements.lock")]
    assert argvs[pip + 1][-2:] == ["-r", str(new / "requirements.txt")]
    backup = argvs.index(["systemctl", "start", "curatables-backup.service"])
    stop = argvs.index(["systemctl", "stop", "curatables.service"])
    assert pip < backup < stop, "build and back up before the old version stops"
    assert any(a[0].endswith("apply-system.sh") for a in argvs)
    assert box["restarts"] == [["systemctl", "restart", "curatables-helper.service"]]
    last = json.loads((SETTINGS.updates_dir / upgrade.RESULT).read_text())
    assert last["state"] == "done" and last["backup"] == "curatables-2026-10-09-030000.db"
    assert not (SETTINGS.updates_dir / upgrade.PENDING).exists()


def test_an_update_that_does_not_start_goes_back(box):
    box["state"]["healthy"] = False
    with pytest.raises(HelperError, match="went back to 0.5.0"):
        upgrade.curatables_update({})(FakeJob())
    assert upgrade.version_of(box["code"]) == "0.5.0"
    assert not upgrade._previous_dir().exists()
    assert not [a for a in box["fake"].argvs() if a[:2] == ["runuser", "-u"]], "no schema change, no restore"
    assert json.loads((SETTINGS.updates_dir / upgrade.RESULT).read_text())["state"] == "failed"
    assert not (SETTINGS.updates_dir / upgrade.PENDING).exists()


def test_a_failed_update_that_changed_the_database_restores_it(box):
    box["state"]["healthy"] = False
    box["state"]["migration"] = True
    with pytest.raises(HelperError):
        upgrade.curatables_update({})(FakeJob())
    restore = [a for a in box["fake"].argvs() if a[:2] == ["runuser", "-u"]]
    assert restore and restore[0][-1].endswith("curatables-2026-10-09-030000.db")


def test_a_checksum_mismatch_changes_nothing(box):
    box["state"]["bad_sum"] = True
    box["state"]["sums"] = "0" * 64 + "  curatables-0.6.0.tar.gz\n"
    with pytest.raises(HelperError, match="checksum"):
        upgrade.curatables_update({})(FakeJob())
    assert upgrade.version_of(box["code"]) == "0.5.0" and not box["code"].is_symlink()
    assert not box["fake"].ran("systemctl", "stop", "curatables.service")


def test_failed_package_install_changes_nothing(box):
    box["fake"].on([str(upgrade._release_dir("0.6.0") / ".venv" / "bin" / "python"), "-m", "pip"], rc=1)
    with pytest.raises(HelperError, match="Nothing was changed"):
        upgrade.curatables_update({})(FakeJob())
    assert upgrade.version_of(box["code"]) == "0.5.0" and not upgrade._release_dir("0.6.0").exists()


def test_rollback_swaps_back_and_keeps_todays_data_without_schema_changes(box):
    upgrade.curatables_update({})(FakeJob())
    box["fake"].calls.clear()
    result = upgrade.curatables_rollback({})(FakeJob())
    assert result["to"] == "0.5.0" and not result["restored"]
    assert upgrade.version_of(box["code"]) == "0.5.0"
    assert upgrade.version_of(upgrade._previous_dir()) == "0.6.0"
    assert ["systemctl", "start", "curatables-backup.service"] in box["fake"].argvs()
    assert not any(a[:2] == ["runuser", "-u"] for a in box["fake"].argvs())


def test_rollback_after_a_schema_change_restores_the_pre_update_backup(box):
    box["state"]["migration"] = True
    upgrade.curatables_update({})(FakeJob())
    result = upgrade.curatables_rollback({})(FakeJob())
    assert result["restored"]
    assert any(a[:2] == ["runuser", "-u"] for a in box["fake"].argvs())


def test_no_previous_version_no_rollback(box):
    with pytest.raises(HelperError, match="no previous version"):
        upgrade.curatables_rollback({})


def test_an_unconfirmed_update_is_undone_when_the_helper_starts(box):
    upgrade.curatables_update({})(FakeJob())
    old = upgrade._real(upgrade._previous_dir())
    old.chmod(0o755)
    upgrade._write_pending(to_version="0.6.0", from_version="0.5.0", backup="b.db", schema_changed=False,
                           old_dir=str(old), previous_before="")
    box["state"]["healthy"] = False
    upgrade.resume_pending()
    assert upgrade.version_of(box["code"]) == "0.5.0"
    assert not (SETTINGS.updates_dir / upgrade.PENDING).exists()
    assert json.loads((SETTINGS.updates_dir / upgrade.RESULT).read_text())["state"] == "failed"


def test_an_unconfirmed_update_that_answers_is_kept(box):
    upgrade.curatables_update({})(FakeJob())
    upgrade._write_pending(to_version="0.6.0", from_version="0.5.0", backup="", schema_changed=False,
                           old_dir=str(upgrade._real(upgrade._previous_dir())), previous_before="")
    upgrade.resume_pending()
    assert upgrade.version_of(box["code"]) == "0.6.0"
    assert not (SETTINGS.updates_dir / upgrade.PENDING).exists()


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



def test_a_pending_file_naming_a_folder_elsewhere_is_ignored(box, tmp_path):
    upgrade.curatables_update({})(FakeJob())
    elsewhere = tmp_path / "evil"
    _tree(elsewhere, "0.1.0")
    upgrade._write_pending(to_version="0.6.0", from_version="0.5.0", backup="../../etc/shadow",
                           schema_changed=True, old_dir=str(elsewhere), previous_before="")
    box["state"]["healthy"] = False
    upgrade.resume_pending()
    assert upgrade.version_of(box["code"]) == "0.6.0"
    assert oct(SETTINGS.updates_dir.stat().st_mode & 0o777) == "0o700"
