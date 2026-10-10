"""The Linux login account (the one that can use sudo) and SSH."""

from __future__ import annotations

import ctypes
import ctypes.util
import hmac
import threading
import time

from syshelper import runner
from syshelper.errors import HelperError
from syshelper.registry import action, flag, text
from syshelper.settings import SETTINGS

MIN_PASSWORD = 8
MAX_PASSWORD = 1024
_crypt_lock = threading.Lock()


def _passwd_entries() -> list[list[str]]:
    lines = SETTINGS.passwd_path.read_text().splitlines()
    return [line.split(":") for line in lines if line.count(":") >= 6]


def admin_user() -> str:
    """The person's login: CURATABLES_ADMIN_USER, else the first regular
    account (uid 1000 and up) that is not the service user."""
    users = {e[0]: e for e in _passwd_entries()}
    if SETTINGS.admin_user:
        if SETTINGS.admin_user not in users:
            raise HelperError(f"The account '{SETTINGS.admin_user}' does not exist.")
        return SETTINGS.admin_user
    regular = sorted((int(e[2]), e[0]) for e in users.values()
                     if e[2].isdigit() and 1000 <= int(e[2]) < 60000 and e[0] != SETTINGS.service_user)
    if not regular:
        raise HelperError("There is no login account on this box.")
    return regular[0][1]


def _shell(user: str) -> str:
    for e in _passwd_entries():
        if e[0] == user:
            return e[6]
    return ""


def _shadow_hash(user: str) -> str:
    for line in SETTINGS.shadow_path.read_text().splitlines():
        fields = line.split(":")
        if fields[0] == user and len(fields) > 1:
            return fields[1]
    return ""


def usable(hashed: str) -> bool:
    return bool(hashed) and hashed[0] not in "!*"


def _crypt(password: str, setting: str) -> str | None:
    name = ctypes.util.find_library("crypt") or "libcrypt.so.1"
    lib = ctypes.CDLL(name)
    lib.crypt.restype = ctypes.c_char_p
    lib.crypt.argtypes = [ctypes.c_char_p, ctypes.c_char_p]
    with _crypt_lock:
        out = lib.crypt(password.encode(), setting.encode())
    if not out or out.startswith(b"*"):
        return None
    return str(out.decode())


def password_matches(password: str, hashed: str) -> bool:
    if not usable(hashed):
        return False
    computed = _crypt(password, hashed)
    return computed is not None and hmac.compare_digest(computed, hashed)


def check_new_password(password: str) -> None:
    if len(password) < MIN_PASSWORD:
        raise HelperError(f"The password must be at least {MIN_PASSWORD} characters.")
    if len(password) > MAX_PASSWORD:
        raise HelperError("That password is too long.")
    if any(c in password for c in "\0\n\r"):
        raise HelperError("The password cannot contain line breaks.")


def ssh_enabled() -> bool:
    return runner.run(["systemctl", "is-enabled", "ssh.service"]).out.strip() == "enabled"


@action("account_status")
def account_status(params: dict) -> dict:
    user = admin_user()
    return {"user": user, "has_password": usable(_shadow_hash(user)), "ssh_enabled": ssh_enabled()}


_check_lock = threading.Lock()


@action("account_password_matches")
def account_password_matches(params: dict) -> dict:
    """One check at a time, each taking at least a second, so the socket
    cannot be used to guess the password quickly."""
    password = text(params, "password", max_len=MAX_PASSWORD)
    with _check_lock:
        started = time.monotonic()
        matches = password_matches(password, _shadow_hash(admin_user()))
        time.sleep(max(0.0, 1.0 - (time.monotonic() - started)))
    return {"matches": matches}


@action("account_set_password")
def account_set_password(params: dict) -> dict:
    password = text(params, "password", max_len=MAX_PASSWORD)
    check_new_password(password)
    user = admin_user()
    result = runner.run(["chpasswd"], input=f"{user}:{password}\n")
    if not result.ok:
        raise HelperError("The password could not be changed: " + result.text.replace(password, "***"))
    if _shell(user).endswith("nologin"):
        runner.run(["usermod", "-s", "/bin/bash", user])
    _finish_first_boot_wizard(user)
    return {"user": user}


def _finish_first_boot_wizard(user: str) -> None:
    """Raspberry Pi OS keeps the console login and SSH closed behind a
    "create a user" dialog until a user is set up. Setting the password
    here counts as that, so close the dialog the way Raspberry Pi
    Imager's settings would."""
    if runner.run(["systemctl", "is-enabled", "userconfig.service"]).out.strip() != "enabled":
        return
    runner.run(["cancel-rename", user], timeout=60)
    runner.run(["systemctl", "stop", "userconfig.service"], timeout=30)


@action("ssh_set")
def ssh_set(params: dict) -> dict:
    enabled = flag(params, "enabled")
    verb = ["enable", "--now"] if enabled else ["disable", "--now"]
    result = runner.run(["systemctl", *verb, "ssh.service"], timeout=60)
    if not result.ok:
        raise HelperError("SSH could not be " + ("turned on: " if enabled else "turned off: ") + result.text)
    if not enabled:
        runner.run(["systemctl", "disable", "--now", "ssh.socket"], timeout=30)
    return {"ssh_enabled": ssh_enabled()}
