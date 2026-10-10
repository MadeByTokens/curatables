"""Recovery files on the SD card's boot partition, the part any
Windows, Mac or Linux computer can open.

  curatables-reset.txt     forget the parent dashboard password, so the
                           first-run setup page opens again (and asks
                           for a new Linux password too)
  curatables-diagnose.txt  write curatables-diagnostics-report.txt next
                           to it once the box is up

Each is acted on once and then renamed (…-done.txt), so it does not
repeat on every boot. Anyone holding the SD card can do far more than
this anyway, so the card is treated as the owner's.
"""

from __future__ import annotations

import json
import pwd
import time

from syshelper import diagnostics, safefs
from syshelper.settings import SETTINGS

RESET = "curatables-reset.txt"
DIAGNOSE = "curatables-diagnose.txt"
REPORT = "curatables-diagnostics-report.txt"
LOG = "curatables-firstboot.log"


def _log(message: str) -> None:
    try:
        with open(SETTINGS.boot_dir / LOG, "a") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ", time.gmtime()) + message + "\n")
    except OSError:
        pass


def _done(name: str, note: str) -> None:
    trigger = SETTINGS.boot_dir / name
    done = SETTINGS.boot_dir / name.replace(".txt", "-done.txt")
    try:
        done.unlink(missing_ok=True)
        trigger.rename(done)
        with open(done, "w", newline="\r\n") as f:
            f.write(note + "\nTo do it again, rename this file back to " + name + " and restart the box.\n")
    except OSError:
        pass


def reset_from_boot() -> int:
    if not (SETTINGS.boot_dir / RESET).exists():
        return 0
    try:
        svc = pwd.getpwnam(SETTINGS.service_user)
    except KeyError:
        _log("reset: the service user does not exist; nothing was changed")
        return 1
    path = SETTINGS.data_dir / "config.json"
    cfg = safefs.read_owned_json(path, svc.pw_uid)
    if cfg is None:
        _log("reset: no readable settings file; nothing to reset (setup opens on its own)")
        _done(RESET, "Nothing needed resetting: the box had no parent password yet.")
        return 0
    parent = cfg.setdefault("parent", {})
    parent["password_hash"] = None
    parent["system_setup_done"] = False
    parent["linux_password_reset"] = True
    parent["session_epoch"] = int(parent.get("session_epoch") or 0) + 1
    safefs.write_owned(path, json.dumps(cfg, indent=2), svc.pw_uid, svc.pw_gid)
    _log("reset: the parent password was cleared; open the dashboard to set a new one")
    _done(RESET, time.strftime("Done on %Y-%m-%d %H:%M UTC: the parent password was cleared. ", time.gmtime())
          + "Open http://curatables.local/ within 30 minutes of starting the box to set a new one.")
    return 0


def report_from_boot() -> int:
    if not (SETTINGS.boot_dir / DIAGNOSE).exists():
        return 0
    try:
        diagnostics.write_text_file(str(SETTINGS.boot_dir / REPORT), diagnostics.build_report())
    except OSError as e:
        _log(f"diagnose: could not write the report: {e}")
        return 1
    _log(f"diagnose: wrote {REPORT}")
    _done(DIAGNOSE, time.strftime("Done on %Y-%m-%d %H:%M UTC: see ", time.gmtime()) + REPORT + ".")
    return 0
