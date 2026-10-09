"""HTTP(S) GET for URLs that came from outside (thumbnail links in
video metadata). Only public internet addresses are contacted, so a
crafted link cannot make the server probe the home network."""

from __future__ import annotations

import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_REDIRECTS = 3


class BlockedURL(ValueError):
    """The URL is not http(s) or points at a non-public address."""


def is_public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def check_url(url: str) -> None:
    """Raise BlockedURL unless every address the host resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURL(f"Not an http(s) URL: {url}")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedURL(f"Cannot resolve {parts.hostname}: {e}") from e
    for info in infos:
        if not is_public_address(str(info[4][0])):
            raise BlockedURL(f"{parts.hostname} resolves to a non-public address")


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(_CheckedRedirects)


def fetch_public(url: str, max_bytes: int, timeout: float = 15) -> bytes:
    """GET `url` and return at most max_bytes + 1 bytes of the body."""
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with _opener.open(req, timeout=timeout) as resp:
            data: bytes = resp.read(max_bytes + 1)
            return data
    except urllib.error.URLError as e:
        if isinstance(e.reason, BlockedURL):
            raise e.reason from e
        raise
