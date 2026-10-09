from __future__ import annotations
"""Client for the root system helper (syshelper/, curatables-helper.service).

On the Raspberry Pi image the helper changes the Linux password, Wi-Fi,
drives and services for the dashboard. Elsewhere (a laptop install, the
tests) its socket does not exist and the pages that need it say so.
"""

import json
import os
import socket
from typing import Any

DEFAULT_SOCKET = "/run/curatables-helper/helper.sock"


class HelperUnavailable(Exception):
    """The helper is not installed or not running."""


class HelperFailed(Exception):
    """The helper refused or failed; the message is for the parent."""


class SystemHelper:
    def __init__(self, socket_path: str = DEFAULT_SOCKET, timeout: float = 120):
        self.socket_path = socket_path
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return os.path.exists(self.socket_path)

    def call(self, action: str, **params: Any) -> Any:
        if not self.available:
            raise HelperUnavailable("This only works on the Curatables box.")
        request = json.dumps({"action": action, "params": params}).encode() + b"\n"
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(self.timeout)
                sock.connect(self.socket_path)
                sock.sendall(request)
                buf = b""
                while b"\n" not in buf:
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
        except OSError as e:
            raise HelperUnavailable(f"The system helper is not answering ({e.strerror or e}).") from e
        try:
            reply = json.loads(buf.split(b"\n", 1)[0])
        except ValueError as e:
            raise HelperUnavailable("The system helper sent a broken reply.") from e
        if not reply.get("ok"):
            raise HelperFailed(str(reply.get("error") or "The system helper refused."))
        return reply.get("result")

    def job(self, job_id: str) -> dict:
        result: dict = self.call("job_status", id=job_id)
        return result

    def linux_password_matches(self, password: str) -> bool:
        """True when password is the Linux login's password. False when
        it is not, or when there is no helper to ask."""
        if not self.available:
            return False
        try:
            result = self.call("account_password_matches", password=password)
        except (HelperUnavailable, HelperFailed):
            return False
        return bool(result.get("matches"))


SAME_AS_LINUX = ("This is the box's Linux login password. Choose a different one, "
                 "so that someone who learns one password does not get both.")
SAME_AS_PARENT = ("This is the parent dashboard password. Choose a different one, "
                  "so that someone who learns one password does not get both.")
