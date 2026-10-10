"""Updating Curatables itself from a GitHub release, and the system's
Debian packages, without re-flashing the SD card.

The code lives in /opt/curatables and the library (videos, database,
settings) in the data directory, so an update only ever replaces the
code. It is built next to the running version, switched in, checked,
and switched back (with the database restored) if it does not come up.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tarfile
import time
import urllib.request
from pathlib import Path

from syshelper import fetch, runner, system
from syshelper.errors import HelperError
from syshelper.jobs import Job
from syshelper.registry import action
from syshelper.settings import SETTINGS

MAX_TARBALL = 200 * 1024 * 1024
RESULT = "last-update.json"


# --- versions -----------------------------------------------------------------

def version_of(code_dir: Path) -> str:
    try:
        for line in (code_dir / "app" / "__init__.py").read_text().splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return ""


def _key(version: str) -> tuple:
    parts = re.findall(r"\d+", version)
    return tuple(int(p) for p in parts[:4])


def newer(candidate: str, installed: str) -> bool:
    return bool(candidate) and _key(candidate) > _key(installed)


def _previous_dir() -> Path:
    """A link to the version before the running one."""
    return SETTINGS.code_dir.with_name(SETTINGS.code_dir.name + ".previous")


def _release_dir(version: str) -> Path:
    """Each version gets its own folder, never renamed afterwards (its
    venv's scripts name their interpreter by full path), and
    /opt/curatables is a link to the running one."""
    stamp = time.strftime("%Y%m%d%H%M%S")
    return SETTINGS.code_dir.with_name(f"{SETTINGS.code_dir.name}-{version}-{stamp}")


def _real(path: Path) -> Path:
    return Path(os.path.realpath(path))


def _point(link: Path, target: Path) -> None:
    """Make link point at target in one atomic step."""
    tmp = link.with_name(link.name + ".tmp-link")
    if os.path.lexists(tmp):
        os.unlink(tmp)
    os.symlink(target, tmp)
    os.replace(tmp, link)


def _link_layout() -> None:
    """The image ships /opt/curatables as a plain folder; turn it into a
    link to a versioned folder the first time an update switches."""
    code = SETTINGS.code_dir
    if code.is_symlink() or not code.is_dir():
        return
    dest = code.with_name(f"{code.name}-{version_of(code) or 'original'}-original")
    os.rename(code, dest)
    runner.run(["chmod", "-R", "go-w", str(dest)], timeout=600)
    _point(code, dest)


def _prune(keep: set[Path]) -> None:
    for d in SETTINGS.code_dir.parent.glob(SETTINGS.code_dir.name + "-*"):
        if d.is_dir() and not d.is_symlink() and _real(d) not in keep:
            shutil.rmtree(d, ignore_errors=True)


def _migrations(code_dir: Path) -> set[str]:
    try:
        return {p.name for p in (code_dir / "app" / "db" / "migrations").iterdir()}
    except OSError:
        return set()


def schema_changed(newer: Path, older: Path) -> bool:
    """True when newer adds database migrations older does not know."""
    return bool(_migrations(newer) - _migrations(older))


def latest_release() -> dict:
    data = fetch.get_json(f"https://api.github.com/repos/{SETTINGS.repo}/releases/latest")
    tag = str(data.get("tag_name") or "")
    version = tag.lstrip("vV")
    if not re.fullmatch(r"\d+(\.\d+){1,3}", version):
        raise HelperError("The newest release on GitHub has no version number the box understands.")
    assets = {a.get("name"): a.get("browser_download_url") for a in data.get("assets") or []
              if isinstance(a, dict)}
    return {
        "version": version, "tag": tag,
        "notes": str(data.get("body") or "")[:4000],
        "published": str(data.get("published_at") or "")[:10],
        "tarball": assets.get(f"curatables-{version}.tar.gz"),
        "sums": assets.get("SHA256SUMS"),
    }


def _read_result() -> dict:
    try:
        data = json.loads((SETTINGS.updates_dir / RESULT).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_result(**fields) -> None:
    _updates_dir()
    fields["at"] = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime())
    tmp = SETTINGS.updates_dir / (RESULT + ".tmp")
    tmp.write_text(json.dumps(fields, indent=2))
    os.replace(tmp, SETTINGS.updates_dir / RESULT)


@action("update_info")
def update_info(params: dict) -> dict:
    installed = version_of(SETTINGS.code_dir)
    info: dict = {"installed": installed, "previous": version_of(_previous_dir()),
                  "last": _read_result(), "reboot_required": Path("/run/reboot-required").exists()}
    if params.get("check", True):
        try:
            latest = latest_release()
            info["latest"] = latest["version"]
            info["notes"] = latest["notes"]
            info["published"] = latest["published"]
            info["available"] = newer(latest["version"], installed) and bool(latest["tarball"] and latest["sums"])
        except HelperError as e:
            info["check_error"] = str(e)
    return info


# --- unpacking ----------------------------------------------------------------

def _safe_members(tar: tarfile.TarFile) -> tuple[str, list[tarfile.TarInfo]]:
    """The release's top folder and its members with that folder
    stripped. Refuses links, devices and paths that leave the folder."""
    members = tar.getmembers()
    tops = {m.name.split("/", 1)[0] for m in members}
    if len(tops) != 1:
        raise HelperError("The release file is not laid out as expected.")
    top = tops.pop()
    out = []
    for m in members:
        rel = m.name.split("/", 1)[1] if "/" in m.name else ""
        if not rel:
            continue
        unsafe_link = m.issym() and (os.path.isabs(m.linkname) or ".." in Path(m.linkname).parts)
        if unsafe_link or m.islnk() or m.isdev() or m.isfifo() or os.path.isabs(rel) or ".." in Path(rel).parts:
            raise HelperError(f"The release file contains something unsafe ({m.name}).")
        m.name = rel
        out.append(m)
    return top, out


def unpack(tarball: Path, dest: Path) -> None:
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    with tarfile.open(tarball, "r:gz") as tar:
        _top, members = _safe_members(tar)
        if hasattr(tarfile, "data_filter"):
            tar.extractall(dest, members=members, filter="data")
        else:
            tar.extractall(dest, members=members)  # noqa: S202  (members checked above)
    if not (dest / "app" / "__init__.py").exists() or not (dest / "scripts" / "apply-system.sh").exists():
        raise HelperError("The release file does not contain Curatables.")


def _expected_sum(sums_text: str, name: str) -> str:
    for line in sums_text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[1].lstrip("*") == name and re.fullmatch(r"[0-9a-f]{64}", parts[0]):
            return parts[0]
    raise HelperError("The release's checksum list does not mention its own file.")


# --- switching versions -------------------------------------------------------

def _healthy(version: str, seconds: int = 180) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(SETTINGS.health_url, timeout=5) as resp:
                if resp.status == 200 and json.loads(resp.read()).get("version") == version:
                    return True
        except (OSError, ValueError):
            pass
        time.sleep(3)
    return False


def _newest_backup() -> str:
    try:
        names = sorted(p.name for p in SETTINGS.backup_dir.glob("curatables-*.db"))
    except OSError:
        return ""
    return names[-1] if names else ""


def _backup(job: Job) -> str:
    job.log("Saving a backup of the settings and the video list")
    result = runner.run(["systemctl", "start", "curatables-backup.service"], timeout=600)
    if not result.ok:
        raise HelperError("The backup before updating failed, so nothing was changed: " + result.text)
    return _newest_backup()


def _restore(job: Job, backup: str) -> None:
    if not backup:
        return
    job.log("Putting back the settings and video list from before the update")
    restore = SETTINGS.code_dir / "scripts" / "restore.sh"
    runner.stream(["runuser", "-u", SETTINGS.service_user, "--", "env",
                   f"CURATABLES_DATA_DIR={SETTINGS.data_dir}", str(restore),
                   str(SETTINGS.backup_dir / backup)], job.log)


def _apply_system(job: Job) -> None:
    rc = runner.stream([str(SETTINGS.code_dir / "scripts" / "apply-system.sh"), str(SETTINGS.code_dir)], job.log)
    if rc != 0:
        job.log("Warning: refreshing the system files reported a problem")


def _switch(job: Job, incoming: Path) -> Path:
    """Make incoming the running code and the current code the previous
    one. Returns the folder that was running."""
    runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
    _link_layout()
    outgoing = _real(SETTINGS.code_dir)
    runner.run(["chown", "-R", "root:root", str(incoming)], timeout=600)
    runner.run(["chmod", "-R", "go-w", str(incoming)], timeout=600)
    _point(SETTINGS.code_dir, incoming)
    _point(_previous_dir(), outgoing)
    _apply_system(job)
    runner.run(["systemctl", "start", "curatables.service"], timeout=120)
    return outgoing


PENDING = "pending-update.json"


def _updates_dir() -> Path:
    SETTINGS.updates_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(SETTINGS.updates_dir, 0o700)
    return SETTINGS.updates_dir


def _write_pending(**fields) -> None:
    (_updates_dir() / PENDING).write_text(json.dumps(fields))


def _trusted_release_dir(raw: object) -> Path | None:
    """A version folder named in the pending file, only if it really is
    one: a root-owned, not group/other-writable sibling of the code link."""
    if not isinstance(raw, str) or not raw:
        return None
    path = _real(Path(raw))
    if path.parent != _real(SETTINGS.code_dir.parent) or not path.name.startswith(SETTINGS.code_dir.name + "-"):
        return None
    try:
        st = os.lstat(path)
    except OSError:
        return None
    if not path.is_dir() or st.st_mode & 0o022 or (os.geteuid() == 0 and st.st_uid != 0):
        return None
    return path


def _clear_pending() -> None:
    (SETTINGS.updates_dir / PENDING).unlink(missing_ok=True)


def _go_back(job: Job, *, to: Path, previous: Path | None, restore_backup: str) -> None:
    """Return to the folder `to` after a failed update."""
    runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
    _point(SETTINGS.code_dir, to)
    if previous is not None and previous.is_dir():
        _point(_previous_dir(), previous)
    elif os.path.lexists(_previous_dir()):
        os.unlink(_previous_dir())
    _apply_system(job)
    _restore(job, restore_backup)
    runner.run(["systemctl", "start", "curatables.service"], timeout=120)


def resume_pending() -> None:
    """Run when the helper starts. An update that was switched in but
    never confirmed (the power went out during its check) is checked
    again, and undone if the new version still does not answer."""
    try:
        pending = json.loads((SETTINGS.updates_dir / PENDING).read_text())
    except (OSError, ValueError):
        return
    target = str(pending.get("to_version") or "")
    if _healthy(target, seconds=300):
        _write_result(state="done", from_version=pending.get("from_version"), to_version=target,
                      backup=pending.get("backup"), message=f"Updated to {target}.")
        _clear_pending()
        return

    class _Log:
        def log(self, message: str) -> None:
            pass
    old = _trusted_release_dir(pending.get("old_dir"))
    if old is not None:
        before = _trusted_release_dir(pending.get("previous_before"))
        backup = str(pending.get("backup") or "")
        if not (re.fullmatch(r"curatables-\d{4}-\d{2}-\d{2}-\d{6}\.db", backup)
                and (SETTINGS.backup_dir / backup).is_file()):
            backup = ""
        _go_back(_Log(), to=old, previous=before,  # type: ignore[arg-type]
                 restore_backup=backup if pending.get("schema_changed") is True else "")
        _write_result(state="failed", from_version=pending.get("from_version"), to_version=target,
                      message=f"{target} did not start after a restart, so the box went back to "
                              f"{pending.get('from_version')}.")
    _clear_pending()


def _restart_helpers(job: Job) -> None:
    runner.run(["systemctl", "try-restart", "curatables-mdns-unicast.service"], timeout=60)
    job.log("The system helper restarts to finish; this page then shows the result")
    system.later(["systemctl", "restart", "curatables-helper.service"], delay=4)


@action("curatables_update", job=True, title="Updating Curatables")
def curatables_update(params: dict):
    installed = version_of(SETTINGS.code_dir)
    latest = latest_release()
    if not newer(latest["version"], installed):
        raise HelperError(f"Curatables {installed} is already the newest version.")
    if not (latest["tarball"] and latest["sums"]):
        raise HelperError(f"Release {latest['version']} has no update file for the box yet. Try again later.")

    def work(job: Job) -> dict:
        target = latest["version"]
        work_dir = SETTINGS.updates_dir / target
        work_dir.mkdir(parents=True, exist_ok=True)
        tarball = work_dir / f"curatables-{target}.tar.gz"
        job.log(f"Downloading Curatables {target}")
        digest = fetch.download(latest["tarball"], tarball, max_bytes=MAX_TARBALL)
        if digest != _expected_sum(fetch.get_text(latest["sums"]), tarball.name):
            raise HelperError("The download does not match its checksum, so it was not used. Try again.")
        job.log("Unpacking")
        new = _release_dir(target)
        unpack(tarball, new)
        if version_of(new) != target:
            shutil.rmtree(new, ignore_errors=True)
            raise HelperError(f"The release file says it is version {version_of(new) or '?'}, not {target}.")
        job.log("Installing its Python packages (this takes a few minutes; kids can keep watching)")
        rc = runner.stream(["python3", "-m", "venv", str(new / ".venv")], job.log, timeout=600)
        if rc == 0:
            rc = runner.stream([str(new / ".venv" / "bin" / "python"), "-m", "pip", "install", "--no-input",
                                "-r", str(new / "requirements.txt")], job.log, timeout=3600)
        if rc != 0:
            shutil.rmtree(new, ignore_errors=True)
            raise HelperError("Installing the new version's packages failed. Nothing was changed.")
        job.log("Checking the new version")
        if runner.stream([str(new / ".venv" / "bin" / "python"), str(new / "run.py"), "--check"],
                         job.log, timeout=300) != 0:
            shutil.rmtree(new, ignore_errors=True)
            raise HelperError("The new version failed its self-check. Nothing was changed.")
        backup = _backup(job)
        changed = schema_changed(new, _real(SETTINGS.code_dir))
        previous_before = _real(_previous_dir()) if os.path.lexists(_previous_dir()) else None
        job.log("Switching to the new version (kids' videos pause for a minute)")
        _write_pending(to_version=target, from_version=installed, backup=backup, schema_changed=changed,
                       old_dir=str(_real(SETTINGS.code_dir) if SETTINGS.code_dir.is_symlink() else ""),
                       previous_before=str(previous_before or ""))
        old = _switch(job, new)
        _write_pending(to_version=target, from_version=installed, backup=backup, schema_changed=changed,
                       old_dir=str(old), previous_before=str(previous_before or ""))
        if not _healthy(target):
            job.log("The new version did not start; going back")
            _go_back(job, to=old, previous=previous_before, restore_backup=backup if changed else "")
            _clear_pending()
            _write_result(state="failed", from_version=installed, to_version=target, backup=backup,
                          message=f"{target} did not start, so the box went back to {installed}.")
            raise HelperError(f"Curatables {target} did not start, so the box went back to {installed}. "
                              "Nothing was lost. The diagnostics report has details.")
        _clear_pending()
        shutil.rmtree(work_dir, ignore_errors=True)
        _prune({_real(SETTINGS.code_dir), _real(_previous_dir())})
        _write_result(state="done", from_version=installed, to_version=target, backup=backup,
                      schema_changed=changed, message=f"Updated from {installed} to {target}.")
        job.log(f"Curatables {target} is running")
        _restart_helpers(job)
        return {"from": installed, "to": target}

    return work


@action("curatables_rollback", job=True, title="Going back to the previous version")
def curatables_rollback(params: dict):
    previous = version_of(_previous_dir())
    if not previous or not _previous_dir().is_dir():
        raise HelperError("There is no previous version to go back to.")
    installed = version_of(SETTINGS.code_dir)
    last = _read_result()

    def work(job: Job) -> dict:
        current, earlier = _real(SETTINGS.code_dir), _real(_previous_dir())
        must_restore = schema_changed(current, earlier)
        safety = _backup(job)
        backup = str(last.get("backup") or "") if must_restore else ""
        if must_restore and not (last.get("from_version") == previous and backup):
            raise HelperError("This update changed the database and its backup is gone, so going back "
                              "is not safe. Nothing was changed.")
        if must_restore:
            job.log("The update changed the database, so the settings and video list from before it "
                    f"come back too (today's are kept in the backup {safety})")
        runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
        _point(SETTINGS.code_dir, earlier)
        _point(_previous_dir(), current)
        _apply_system(job)
        _restore(job, backup)
        runner.run(["systemctl", "start", "curatables.service"], timeout=120)
        ok = _healthy(previous)
        _write_result(state="rolled_back" if ok else "failed", from_version=installed, to_version=previous,
                      backup=safety, message=f"Went back from {installed} to {previous}." if ok
                      else f"Went back to {previous}, but it did not answer yet.")
        _restart_helpers(job)
        return {"from": installed, "to": previous, "healthy": ok, "restored": bool(backup)}

    return work


# --- Debian packages ----------------------------------------------------------

APT_ENV = {"DEBIAN_FRONTEND": "noninteractive", "NEEDRESTART_MODE": "a"}


@action("system_update", job=True, title="Updating system packages")
def system_update(params: dict):
    def work(job: Job) -> dict:
        job.log("Looking for updates")
        if runner.stream(["apt-get", "update"], job.log, timeout=900, env=APT_ENV) != 0:
            raise HelperError("Could not fetch the list of updates. Check the internet connection, "
                              "or wait a few minutes if automatic security updates are running.")
        job.log("Installing updates (this can take a while)")
        rc = runner.stream(["apt-get", "-y", "-o", "Dpkg::Options::=--force-confdef",
                            "-o", "Dpkg::Options::=--force-confold", "full-upgrade"],
                           job.log, timeout=3600, env=APT_ENV)
        if rc != 0:
            raise HelperError("Installing the updates failed. Try again in a few minutes; "
                              "the diagnostics report has details.")
        runner.run(["apt-get", "-y", "autoremove"], timeout=900)
        reboot = Path("/run/reboot-required").exists()
        job.log("Done. Restart the box to finish." if reboot else "Done.")
        return {"reboot_required": reboot}

    return work
