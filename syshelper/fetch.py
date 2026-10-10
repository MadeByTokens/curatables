"""HTTPS downloads for the updater, IPv4 first.

Many home networks hand out IPv6 addresses that do not actually route,
and every connection then stalls before falling back. GitHub serves
both, so IPv4 addresses are tried first.
"""

from __future__ import annotations

import hashlib
import json
import socket
import urllib.request
from pathlib import Path

from syshelper.errors import HelperError

USER_AGENT = "curatables-updater"
_real_getaddrinfo = socket.getaddrinfo


def _ipv4_first(*args, **kwargs):
    results = _real_getaddrinfo(*args, **kwargs)
    return sorted(results, key=lambda r: 0 if r[0] == socket.AF_INET else 1)


socket.getaddrinfo = _ipv4_first  # type: ignore[assignment]


def _open(url: str, timeout: float):
    if not url.startswith("https://"):
        raise HelperError("Updates are only downloaded over HTTPS.")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "application/vnd.github+json, */*"})
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except OSError as e:
        raise HelperError(f"Could not reach GitHub ({e}). Check the internet connection "
                          "on Box, Diagnostics.") from e


def get_json(url: str, *, max_bytes: int = 2 * 1024 * 1024, timeout: float = 30) -> dict:
    with _open(url, timeout) as resp:
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise HelperError("GitHub sent an unexpectedly large answer.")
    try:
        data = json.loads(body)
    except ValueError as e:
        raise HelperError("GitHub sent an answer that could not be read.") from e
    if not isinstance(data, dict):
        raise HelperError("GitHub sent an answer that could not be read.")
    return data


def get_text(url: str, *, max_bytes: int = 64 * 1024, timeout: float = 30) -> str:
    with _open(url, timeout) as resp:
        body = resp.read(max_bytes + 1)
    if len(body) > max_bytes:
        raise HelperError("A release file is unexpectedly large.")
    return str(body.decode("utf-8", "replace"))


def download(url: str, dest: Path, *, max_bytes: int, timeout: float = 60) -> str:
    """Save url to dest and return its SHA-256."""
    digest = hashlib.sha256()
    size = 0
    with _open(url, timeout) as resp, open(dest, "wb") as out:
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > max_bytes:
                raise HelperError("The download is larger than any release should be; stopped.")
            digest.update(chunk)
            out.write(chunk)
    return digest.hexdigest()
