"""HTTP(S) GET for URLs that came from outside (thumbnail links in
video metadata, lookups). Only public internet addresses are contacted,
so a crafted link cannot make the server probe the home network.

Connections go to exactly the addresses that were checked (no second
DNS lookup to rebind), IPv4 first with a short connect timeout per
address, because many home networks hand out IPv6 addresses that do
not route.
"""

from __future__ import annotations

import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from urllib.parse import urlsplit

MAX_REDIRECTS = 3
CONNECT_TIMEOUT = 4.0


class BlockedURL(ValueError):
    """The URL is not http(s) or points at a non-public address."""


def is_public_address(ip: str) -> bool:
    addr = ipaddress.ip_address(ip)
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped:
        addr = addr.ipv4_mapped
    return addr.is_global and not addr.is_multicast


def resolve_public(host: str, port: int) -> list[tuple[int, tuple]]:
    """Every address ``host`` resolves to, IPv4 first; BlockedURL if any
    of them is not a public internet address."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror as e:
        raise BlockedURL(f"Cannot resolve {host}: {e}") from e
    out: list[tuple[int, tuple]] = []
    for family, _, _, _, sockaddr in infos:
        if not is_public_address(str(sockaddr[0])):
            raise BlockedURL(f"{host} resolves to a non-public address")
        if (family, sockaddr) not in out:
            out.append((family, sockaddr))
    out.sort(key=lambda a: a[0] != socket.AF_INET)
    return out


def check_url(url: str) -> None:
    """Raise BlockedURL unless every address the host resolves to is public."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise BlockedURL(f"Not an http(s) URL: {url}")
    resolve_public(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))


def _connect(host: str, port: int, timeout: float | None) -> socket.socket:
    last: OSError | None = None
    for family, sockaddr in resolve_public(host, port):
        sock = socket.socket(family, socket.SOCK_STREAM)
        sock.settimeout(min(timeout or CONNECT_TIMEOUT, CONNECT_TIMEOUT))
        try:
            sock.connect(sockaddr)
        except OSError as e:
            sock.close()
            last = e
            continue
        sock.settimeout(timeout)
        return sock
    raise last or OSError(f"Cannot connect to {host}")


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = _connect(self.host, self.port, self.timeout)


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        sock = _connect(self.host, self.port, self.timeout)
        self.sock = self._context.wrap_socket(sock, server_hostname=self.host)


class _PinnedHTTPHandler(urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(_PinnedHTTPConnection, req)


class _PinnedHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(_PinnedHTTPSConnection, req)


class _CheckedRedirects(urllib.request.HTTPRedirectHandler):
    max_redirections = MAX_REDIRECTS

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}), _PinnedHTTPHandler, _PinnedHTTPSHandler,
    _CheckedRedirects)


def fetch_public(url: str, max_bytes: int, timeout: float = 15,
                 headers: dict[str, str] | None = None) -> bytes:
    """GET `url` and return at most max_bytes + 1 bytes of the body."""
    check_url(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0", **(headers or {})})
    try:
        with _opener.open(req, timeout=timeout) as resp:
            data: bytes = resp.read(max_bytes + 1)
            return data
    except urllib.error.URLError as e:
        if isinstance(e.reason, BlockedURL):
            raise e.reason from e
        raise
