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
    return SETTINGS.code_dir.with_name(SETTINGS.code_dir.name + ".previous")


def _new_dir() -> Path:
    return SETTINGS.code_dir.with_name(SETTINGS.code_dir.name + ".new")


def _failed_dir() -> Path:
    return SETTINGS.code_dir.with_name(SETTINGS.code_dir.name + ".failed")


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
    SETTINGS.updates_dir.mkdir(parents=True, exist_ok=True)
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


def _switch(job: Job, incoming: Path, outgoing_to: Path) -> None:
    """Make incoming the running code; the current code moves to outgoing_to."""
    runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
    if outgoing_to.exists():
        shutil.rmtree(outgoing_to)
    os.rename(SETTINGS.code_dir, outgoing_to)
    os.rename(incoming, SETTINGS.code_dir)
    runner.run(["chown", "-R", "root:root", str(SETTINGS.code_dir)], timeout=600)
    _apply_system(job)
    runner.run(["systemctl", "start", "curatables.service"], timeout=120)


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
        new = _new_dir()
        unpack(tarball, new)
        if version_of(new) != target:
            raise HelperError(f"The release file says it is version {version_of(new) or '?'}, not {target}.")
        job.log("Installing its Python packages (this takes a few minutes; kids can keep watching)")
        rc = runner.stream(["python3", "-m", "venv", str(new / ".venv")], job.log, timeout=600)
        if rc == 0:
            rc = runner.stream([str(new / ".venv" / "bin" / "pip"), "install", "--no-input",
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
        job.log("Switching to the new version (kids' videos pause for a minute)")
        _switch(job, new, _previous_dir())
        if not _healthy(target):
            job.log("The new version did not start; going back")
            runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
            _switch(job, _previous_dir(), _failed_dir())
            runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
            _restore(job, backup)
            runner.run(["systemctl", "start", "curatables.service"], timeout=120)
            _write_result(state="failed", from_version=installed, to_version=target, backup=backup,
                          message=f"{target} did not start, so the box went back to {installed}.")
            raise HelperError(f"Curatables {target} did not start, so the box went back to {installed}. "
                              "Nothing was lost. The diagnostics report has details.")
        shutil.rmtree(work_dir, ignore_errors=True)
        _write_result(state="done", from_version=installed, to_version=target, backup=backup,
                      message=f"Updated from {installed} to {target}.")
        job.log(f"Curatables {target} is running")
        _restart_helpers(job)
        return {"from": installed, "to": target}

    return work


@action("curatables_rollback", job=True, title="Going back to the previous version")
def curatables_rollback(params: dict):
    previous = version_of(_previous_dir())
    if not previous:
        raise HelperError("There is no previous version to go back to.")
    installed = version_of(SETTINGS.code_dir)
    last = _read_result()

    def work(job: Job) -> dict:
        _switch(job, _previous_dir(), _new_dir())
        os.rename(_new_dir(), _previous_dir())
        if last.get("state") == "done" and last.get("from_version") == previous and last.get("backup"):
            runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
            _restore(job, str(last["backup"]))
            runner.run(["systemctl", "start", "curatables.service"], timeout=120)
        ok = _healthy(previous)
        _write_result(state="rolled_back" if ok else "failed", from_version=installed, to_version=previous,
                      message=f"Went back from {installed} to {previous}." if ok
                      else f"Went back to {previous}, but it did not answer yet.")
        _restart_helpers(job)
        return {"from": installed, "to": previous, "healthy": ok}

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
