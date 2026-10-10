"""The socket the web app talks to: one JSON request per connection,
one JSON reply."""

from __future__ import annotations

import json
import logging
import os
import pwd
import socket
import struct
import threading
from typing import Any

from syshelper import accounts, backups, diagnostics, drives, system, upgrade, wifi  # noqa: F401  (they register actions)
from syshelper.errors import HelperError
from syshelper.jobs import JOBS
from syshelper.registry import ACTIONS, text
from syshelper.settings import SETTINGS

logger = logging.getLogger("curatables-helper")
MAX_REQUEST = 64 * 1024


def handle(request: Any) -> dict:
    """Run one request and return the reply. Never raises."""
    try:
        if not isinstance(request, dict):
            raise HelperError("bad request")
        name = request.get("action")
        params = request.get("params") or {}
        if not isinstance(name, str) or not isinstance(params, dict):
            raise HelperError("bad request")
        return {"ok": True, "result": _dispatch(name, params)}
    except HelperError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        logger.exception("request failed")
        return {"ok": False, "error": f"Something unexpected went wrong ({type(e).__name__})."}


def _dispatch(name: str, params: dict) -> Any:
    if name == "ping":
        return {"pong": True}
    if name == "job_status":
        return JOBS.get(text(params, "id", max_len=32)).as_dict()
    if name == "jobs":
        return [j.as_dict() for j in JOBS.latest()]
    act = ACTIONS.get(name)
    if act is None:
        raise HelperError(f"unknown action '{name}'")
    if act.job:
        work = act.fn(params)
        return JOBS.start(name, act.title, work).as_dict()
    return act.fn(params)


def _peer_uid(conn: socket.socket) -> int:
    creds = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
    return int(struct.unpack("3i", creds)[1])


def _serve_one(conn: socket.socket, allowed: set[int]) -> None:
    with conn:
        try:
            if _peer_uid(conn) not in allowed:
                return
            conn.settimeout(15)
            buf = b""
            while b"\n" not in buf and len(buf) <= MAX_REQUEST:
                chunk = conn.recv(8192)
                if not chunk:
                    break
                buf += chunk
            if len(buf) > MAX_REQUEST:
                reply = {"ok": False, "error": "request too large"}
            else:
                try:
                    request = json.loads(buf.split(b"\n", 1)[0] or b"null")
                except ValueError:
                    request = None
                reply = handle(request)
            conn.sendall(json.dumps(reply).encode() + b"\n")
        except OSError as e:
            logger.debug("connection dropped: %s", e)


def serve() -> None:
    path = SETTINGS.socket_path
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o755)
    if path.exists() or path.is_symlink():
        path.unlink()
    svc = pwd.getpwnam(SETTINGS.service_user)
    allowed = {0, svc.pw_uid}
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o177)
    try:
        sock.bind(str(path))
    finally:
        os.umask(old_umask)
    os.chown(path, 0, svc.pw_gid)
    os.chmod(path, 0o660)
    sock.listen(8)
    logger.info("listening on %s for %s", path, SETTINGS.service_user)
    threading.Thread(target=upgrade.resume_pending, daemon=True).start()
    while True:
        conn, _ = sock.accept()
        threading.Thread(target=_serve_one, args=(conn, allowed), daemon=True).start()
