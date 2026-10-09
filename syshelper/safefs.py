"""Reading and writing files in folders the service user controls.

The helper runs as root, but the data directory belongs to the
unprivileged service user. A file there could be swapped for a symlink
to /etc/shadow, so nothing in it is ever opened by following a link,
read unless the service user owns it, or written except through a
freshly created file handed to the service user by its descriptor.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path


def read_owned(path: Path, uid: int, *, tail_bytes: int | None = None) -> str | None:
    """The text of path if it is a regular file owned by uid, else None."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC)
    except OSError:
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not stat.S_ISREG(st.st_mode) or st.st_uid != uid:
            return None
        if tail_bytes is not None and st.st_size > tail_bytes:
            f.seek(st.st_size - tail_bytes)
        return f.read().decode("utf-8", "replace")


def read_owned_json(path: Path, uid: int) -> dict | None:
    content = read_owned(path, uid)
    if content is None:
        return None
    try:
        data = json.loads(content)
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def write_owned(path: Path, content: str, uid: int, gid: int, mode: int = 0o600) -> None:
    """Replace path with content, as a new file owned by uid:gid."""
    tmp = path.with_name(path.name + ".helper-tmp")
    try:
        os.unlink(tmp)
    except FileNotFoundError:
        pass
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, mode)
    with os.fdopen(fd, "w") as f:
        os.fchown(f.fileno(), uid, gid)
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
