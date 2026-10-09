"""The nightly database and settings snapshots: list, make one now,
and restore one."""

from __future__ import annotations

import os
import re
from pathlib import Path

from syshelper import runner
from syshelper.errors import HelperError
from syshelper.jobs import Job
from syshelper.registry import action, text
from syshelper.settings import SETTINGS

NAME = re.compile(r"curatables-(\d{4}-\d{2}-\d{2})-(\d{2})(\d{2})(\d{2})\.db")


def places() -> dict[str, Path]:
    return {"sd": SETTINGS.backup_dir, "drive": SETTINGS.media_mount / "curatables-library" / "backups"}


def _list(where: str, folder: Path) -> list[dict]:
    try:
        entries = list(os.scandir(folder))
    except OSError:
        return []
    out = []
    for entry in entries:
        m = NAME.fullmatch(entry.name)
        if not m or not entry.is_file(follow_symlinks=False):
            continue
        out.append({"where": where, "name": entry.name, "size": entry.stat(follow_symlinks=False).st_size,
                    "when": f"{m.group(1)} {m.group(2)}:{m.group(3)}"})
    return out


@action("backups_list")
def backups_list(params: dict) -> dict:
    found = [b for where, folder in places().items() for b in _list(where, folder)]
    return {"backups": sorted(found, key=lambda b: b["name"], reverse=True)}


@action("backup_now")
def backup_now(params: dict) -> dict:
    result = runner.run(["systemctl", "start", "curatables-backup.service"], timeout=600)
    if not result.ok:
        raise HelperError("The backup failed: " + result.text)
    listing: dict = backups_list({})
    return listing


@action("backup_restore", job=True, title="Restoring a backup")
def backup_restore(params: dict):
    where = text(params, "where", max_len=8)
    name = text(params, "name", max_len=64)
    if where not in places() or not NAME.fullmatch(name):
        raise HelperError("That backup is not known.")
    if not any(b["name"] == name for b in _list(where, places()[where])):
        raise HelperError("That backup is not there any more.")
    path = places()[where] / name
    restore = SETTINGS.code_dir / "scripts" / "restore.sh"

    def work(job: Job) -> dict:
        job.log("Pausing Curatables")
        runner.run(["systemctl", "stop", "curatables.service"], timeout=120)
        try:
            job.log(f"Restoring the snapshot from {name[11:21]}")
            rc = runner.stream(["runuser", "-u", SETTINGS.service_user, "--", "env",
                                f"CURATABLES_DATA_DIR={SETTINGS.data_dir}", str(restore), str(path)], job.log)
            if rc != 0:
                raise HelperError("The backup could not be restored; nothing was changed.")
        finally:
            job.log("Starting Curatables again")
            runner.run(["systemctl", "start", "curatables.service"], timeout=120)
        job.log("Restored. Sign in again if the dashboard asks.")
        return {"restored": name}

    return work
