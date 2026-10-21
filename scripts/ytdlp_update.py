"""Update yt-dlp only to a version that works: the root side of the
"Update the downloader" button. scripts/updater.sh runs it as root with
the system Python when the app drops its request flag (see
app/services/updates.py).

1. Download the newest yt-dlp and its YouTube challenge solver as wheel
   files into a staging folder, and copies of the versions in use (to go
   back to).
2. Check both the way Curatables uses them (scripts/ytdlp-check.py, run
   as the service user): load, a video's details, a search, a channel's
   list, and the first bytes of the file a download would fetch.
3. Switch only if the new version passes. If it fails where the one in
   use passes, keep the one in use; if both fail it is most likely the
   network or YouTube, so keep it and say to try later. A new version
   that passes while the one in use fails is the usual fix for a YouTube
   change, so it goes in.
4. Wait for running downloads to finish (a few minutes at most), install
   exactly the files that were checked, restart Curatables and check it
   again; if it does not come back, put the previous files back.

It runs on the box, so leaving or closing the page changes nothing: the
page shows the result whenever it is opened again. The request flag is
removed only at the end, so after a power cut the path unit runs this
again at boot, and a switch that was under way is finished or undone
from the note in STATE_DIR.

Root only ever runs the system Python and fixed commands. pip runs as
the venv's owner and installs wheels, which run no code. The downloaded
code runs only as the service user inside curatables.service's sandbox
(see SANDBOX). Every file in the data dir is read and written as the
service user, so a planted symlink there cannot redirect root.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(os.environ.get("CURATABLES_ROOT", "/opt/curatables"))
DATA = Path(os.environ.get("CURATABLES_DATA", "/home/curatables/curatables-data"))
SVC_USER = os.environ.get("CURATABLES_USER", "curatables")
STATE_DIR = Path(os.environ.get("CURATABLES_YTDLP_STATE", "/var/lib/curatables-ytdlp"))
SERVICE = os.environ.get("CURATABLES_SERVICE", "curatables.service")

REQUEST = "update-request.json"
RESULT = "update-result.json"
PROGRESS = "update-progress.json"
NOTE = "switching.json"

PACKAGES = {"yt-dlp": "yt-dlp>=2024.0", "yt-dlp-ejs": "yt-dlp-ejs>=0.8"}
WAIT_FOR_DOWNLOADS = 300
CHECK_TIMEOUT = 300
SETTLE_SECONDS = 120

# What a parent reads when a check fails.
CHECK_NAMES = {"loads": "load", "solver": "solve YouTube's challenge", "settings": "take our settings",
               "details": "read a video's details", "search": "search", "channel": "list a channel's videos",
               "download": "download videos"}


class Failed(Exception):
    """The update stopped; the message is for the parent."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(argv: list[str], *, user: str | None = None, timeout: float = 600,
        input: str | None = None) -> subprocess.CompletedProcess:
    if user and user != "root":
        argv = ["runuser", "-u", user, "--", *argv]
    try:
        return subprocess.run(argv, capture_output=True, text=True, timeout=timeout, input=input)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(argv, 124, "", f"did not finish within {int(timeout)} seconds")


# --- the data dir, as the service user ---------------------------------------

def read_data(name: str) -> str | None:
    r = run(["cat", "--", str(DATA / name)], user=SVC_USER, timeout=30)
    return r.stdout if r.returncode == 0 else None


def write_data(name: str, payload: dict) -> None:
    path = str(DATA / name)
    run(["sh", "-c", 'umask 022; cat > "$1.tmp" && mv -f "$1.tmp" "$1"', "_", path],
        user=SVC_USER, timeout=30, input=json.dumps(payload, indent=2) + "\n")


def remove_data(name: str) -> None:
    run(["rm", "-f", "--", str(DATA / name)], user=SVC_USER, timeout=30)


def progress(stage: str) -> None:
    print(stage, flush=True)
    write_data(PROGRESS, {"stage": stage, "at": _now()})


def write_result(status: str, message: str, before: dict, after: dict, checks: dict | None = None) -> None:
    write_data(RESULT, {"kind": "yt-dlp", "status": status, "message": message,
                        "from_version": before.get("yt-dlp") or None,
                        "to_version": after.get("yt-dlp") or None,
                        "checks": checks or {}, "finished_at": _now()})


# --- versions and wheels -----------------------------------------------------

def venv_python() -> str:
    return str(ROOT / ".venv" / "bin" / "python")


def venv_owner() -> str:
    return pwd.getpwuid(os.stat(ROOT / ".venv").st_uid).pw_name


def installed() -> dict:
    code = ("import json, importlib.metadata as m\n"
            "out = {}\n"
            f"for name in {sorted(PACKAGES)!r}:\n"
            "    try: out[name] = m.version(name)\n"
            "    except m.PackageNotFoundError: pass\n"
            "print(json.dumps(out))")
    r = run([venv_python(), "-I", "-c", code], user=SVC_USER, timeout=60)
    try:
        return json.loads(r.stdout)
    except ValueError:
        return {}


_WHEEL = re.compile(r"^(?P<name>[A-Za-z0-9_.]+)-(?P<version>[^-]+)-.+\.whl$")


def wheel_versions(folder: Path) -> dict:
    """{"yt-dlp": "2026.8.19", ...} from the wheel file names."""
    out = {}
    for path in folder.glob("*.whl"):
        m = _WHEEL.match(path.name)
        if m:
            out[m["name"].replace("_", "-").lower()] = m["version"]
    return out


def same(a: dict, b: dict) -> bool:
    def norm(v: str) -> tuple:
        return tuple(int(x) for x in re.findall(r"\d+", v or ""))
    return all(norm(a.get(k, "")) == norm(b.get(k, "")) for k in PACKAGES)


def pip(args: list[str], timeout: float = 900) -> subprocess.CompletedProcess:
    return run([venv_python(), "-m", "pip", "--disable-pip-version-check", "--no-input", *args],
               user=venv_owner(), timeout=timeout)


def _tail(r: subprocess.CompletedProcess) -> str:
    return " ".join((r.stdout + r.stderr).strip().splitlines()[-3:])[:400]


def fresh_dir(path: Path) -> Path:
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir(parents=True)
    owner = pwd.getpwnam(venv_owner())
    os.chown(path, owner.pw_uid, owner.pw_gid)
    os.chmod(path, 0o755)
    return path


# --- checking ------------------------------------------------------------------

# The downloaded code runs only like this: as the service user, inside the
# same walls as curatables.service (it can write the data dir, nothing
# else), so checking a release is no riskier than running it.
SANDBOX = ("NoNewPrivileges=yes", "PrivateTmp=yes", "ProtectSystem=strict", "ProtectHome=read-only",
           "ProtectKernelTunables=yes", "ProtectKernelModules=yes", "ProtectControlGroups=yes",
           "RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK", "RestrictNamespaces=yes",
           "LockPersonality=yes")


def sandboxed(argv: list[str]) -> list[str]:
    props = [f"--property={p}" for p in (f"User={SVC_USER}", f"Group={SVC_USER}",
                                          f"ReadWritePaths={DATA}", *SANDBOX)]
    return ["systemd-run", "--quiet", "--wait", "--pipe", "--collect", f"--working-directory={ROOT}",
            *props, "--", *argv]


def check(site: Path | None = None, offline: bool = False) -> dict:
    argv = [venv_python(), "-I", str(ROOT / "scripts" / "ytdlp-check.py"), str(DATA)]
    if site is not None:
        argv += ["--site", str(site)]
    if offline:
        argv.append("--offline")
    r = run(sandboxed(argv), timeout=CHECK_TIMEOUT)
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return {"ok": False, "version": "", "checks": [
            {"name": "loads", "ok": False, "detail": _tail(r) or "the check did not run"}]}


def failures(result: dict) -> list[str]:
    return [CHECK_NAMES.get(c.get("name"), c.get("name")) for c in result.get("checks", []) if not c.get("ok")]


def decide(current: dict, new: dict) -> str:
    """'switch' or 'keep'. Only the new version's check decides: one that
    passes goes in, also when the one in use fails (the usual fix for a
    YouTube change). The current one's check says why a failing new one
    was kept (see keep_message)."""
    return "switch" if new.get("ok") else "keep"


def keep_message(current: dict, new: dict, before: dict, target: dict) -> str:
    cur, nxt = before.get("yt-dlp", "?"), target.get("yt-dlp", "?")
    if current.get("ok"):
        return (f"Kept {cur}: the new {nxt} could not {', '.join(failures(new))}. "
                "Nothing was changed; the next release will be tried when you press the button again.")
    return (f"Kept {cur}: neither it nor the new {nxt} could {', '.join(failures(new))}, which usually "
            "means the internet or YouTube is having trouble. Nothing was changed; try again later.")


# --- switching -----------------------------------------------------------------

def wait_for_downloads() -> None:
    deadline = time.monotonic() + WAIT_FOR_DOWNLOADS
    while time.monotonic() < deadline:
        r = run([venv_python(), "-I", str(ROOT / "scripts" / "ytdlp-check.py"), str(DATA), "--busy"],
                user=SVC_USER, timeout=60)
        try:
            if json.loads(r.stdout).get("downloading", 0) == 0:
                return
        except ValueError:
            return
        progress("Waiting for a download to finish")
        time.sleep(10)


def install(wheels: Path) -> bool:
    files = sorted(str(p) for p in wheels.glob("*.whl"))
    return bool(files) and pip(["install", "--no-deps", "--no-index", "--force-reinstall", *files]).returncode == 0


def settled(target: dict) -> bool:
    """Curatables is running the target versions, and they load."""
    deadline = time.monotonic() + SETTLE_SECONDS
    while time.monotonic() < deadline:
        active = run(["systemctl", "is-active", "--quiet", SERVICE], timeout=30).returncode == 0
        if active and same(installed(), target) and check(offline=True).get("ok"):
            time.sleep(10)  # still up a little later, not in a restart loop
            if run(["systemctl", "is-active", "--quiet", SERVICE], timeout=30).returncode == 0:
                return True
        time.sleep(5)
    return False


def restart() -> None:
    run(["systemctl", "restart", SERVICE], timeout=180)


def write_note(note: dict) -> None:
    (STATE_DIR / NOTE).write_text(json.dumps(note))


def clear_note() -> None:
    (STATE_DIR / NOTE).unlink(missing_ok=True)


def go_back(previous: Path) -> bool:
    run(["systemctl", "stop", SERVICE], timeout=180)
    ok = install(previous)
    run(["systemctl", "start", SERVICE], timeout=180)
    return ok


def switch(new_wheels: Path, prev_wheels: Path, before: dict, target: dict, checks: dict) -> None:
    progress("Switching to the new version (videos pause for a moment)")
    write_note({"before": before, "target": target})
    run(["systemctl", "stop", SERVICE], timeout=180)
    if not install(new_wheels):
        go_back(prev_wheels)
        clear_note()
        raise Failed(f"Installing {target.get('yt-dlp')} failed, so {before.get('yt-dlp')} was put back.")
    run(["systemctl", "start", SERVICE], timeout=180)
    progress("Checking Curatables with the new version")
    if not settled(target):
        go_back(prev_wheels)
        clear_note()
        raise Failed(f"Curatables did not run well with {target.get('yt-dlp')}, so {before.get('yt-dlp')} "
                     "was put back.")
    clear_note()
    write_result("ok", f"Updated from {before.get('yt-dlp')} to {target.get('yt-dlp')}, after checking that "
                 "it reads video details, searches, lists channels and downloads.", before, target, checks)


def resume(note: dict) -> None:
    """A switch the power cut off: finish it if the new version is in and
    works, else put the previous one back."""
    before, target = note.get("before") or {}, note.get("target") or {}
    restart()
    if settled(target):
        write_result("ok", f"Updated from {before.get('yt-dlp')} to {target.get('yt-dlp')} "
                     "(finished after the box restarted).", before, target)
    else:
        go_back(STATE_DIR / "previous")
        write_result("error", f"The update to {target.get('yt-dlp')} was interrupted, so "
                     f"{before.get('yt-dlp')} was put back.", before, before)
    clear_note()


# --- the whole update ------------------------------------------------------------

def update() -> None:
    before = installed()
    progress("Downloading the newest version")
    new_wheels = fresh_dir(STATE_DIR / "new")
    r = pip(["download", "--no-deps", "--only-binary=:all:", "-d", str(new_wheels), *PACKAGES.values()])
    if r.returncode != 0:
        raise Failed("The new version could not be downloaded. Check the internet connection and try again. "
                     + _tail(r))
    target = wheel_versions(new_wheels)
    if not all(k in target for k in PACKAGES):
        raise Failed("The download did not have every part of the new version. Try again later.")
    if same(before, target):
        write_result("ok", "Already the newest version.", before, before)
        return
    prev_wheels = fresh_dir(STATE_DIR / "previous")
    pinned = [f"{k}=={v}" for k, v in before.items() if k in PACKAGES]
    r = pip(["download", "--no-deps", "--only-binary=:all:", "-d", str(prev_wheels), *pinned])
    if r.returncode != 0 or not pinned:
        raise Failed("A copy of the version in use, to go back to, could not be downloaded, so nothing was "
                     "changed. Try again later. " + _tail(r))
    site = fresh_dir(STATE_DIR / "site")
    r = pip(["install", "--no-deps", "--no-index", "--target", str(site),
             *sorted(str(p) for p in new_wheels.glob("*.whl"))])
    if r.returncode != 0:
        raise Failed("The new version could not be unpacked for checking. Nothing was changed. " + _tail(r))
    progress(f"Checking the new version {target['yt-dlp']} (about a minute)")
    new = check(site)
    progress(f"Checking the version in use {before.get('yt-dlp')}")
    current = check()
    checks = {"new": new, "current": current}
    if decide(current, new) == "keep":
        write_result("kept", keep_message(current, new, before, target), before, before, checks)
        return
    wait_for_downloads()
    switch(new_wheels, prev_wheels, before, target, checks)


def main() -> int:
    if read_data(REQUEST) is None:
        return 0  # the flag vanished between the trigger and now
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    os.chmod(STATE_DIR, 0o755)
    try:
        note = json.loads((STATE_DIR / NOTE).read_text())
    except (OSError, ValueError):
        note = None
    try:
        if note is not None:
            resume(note)
            return 0
        try:
            kind = json.loads(read_data(REQUEST) or "{}").get("kind", "")
        except (ValueError, AttributeError):
            kind = ""
        if kind != "yt-dlp":
            write_result("error", f"unsupported update kind: {kind or '<none>'}", {}, {})
            return 0
        try:
            update()
        except Exception as e:
            if (STATE_DIR / NOTE).exists():  # stopped part way through the switch
                go_back(STATE_DIR / "previous")
                clear_note()
            before = installed()
            message = str(e) if isinstance(e, Failed) else f"The update stopped unexpectedly ({e}). " \
                "The version in use was kept."
            write_result("error", message, before, before)
            return 1
        return 0
    finally:
        for name in ("site", "new"):
            shutil.rmtree(STATE_DIR / name, ignore_errors=True)
        remove_data(PROGRESS)
        remove_data(REQUEST)


if __name__ == "__main__":
    sys.exit(main())
