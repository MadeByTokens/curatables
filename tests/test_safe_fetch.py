"""Outbound fetches of externally supplied URLs stay on the public internet."""

import socket
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app.services import safe_fetch
from app.services.safe_fetch import BlockedURL, check_url, fetch_public, is_public_address


@pytest.mark.parametrize("ip", [
    "127.0.0.1", "10.0.0.5", "192.168.200.1", "172.16.4.4", "169.254.169.254",
    "100.64.0.1", "0.0.0.0", "::1", "fe80::1", "fc00::1", "::ffff:192.168.200.1",
    "224.0.0.251",
])
def test_private_and_special_addresses_are_not_public(ip):
    assert not is_public_address(ip)


@pytest.mark.parametrize("ip", ["8.8.8.8", "142.250.72.14", "2606:4700:4700::1111"])
def test_internet_addresses_are_public(ip):
    assert is_public_address(ip)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x.jpg", "http://localhost:8080/", "http://192.168.77.1/admin",
    "http://[::1]/", "file:///etc/passwd", "ftp://example.com/x", "http:///nohost",
])
def test_check_url_blocks_local_targets(url):
    with pytest.raises(BlockedURL):
        check_url(url)


def test_hostname_resolving_to_lan_is_blocked(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("192.168.200.20", 80)),
    ])
    with pytest.raises(BlockedURL):
        check_url("http://thumbs.example.com/a.jpg")


class _Redirector(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(302)
        self.send_header("Location", "http://127.0.0.1:1/secret")
        self.end_headers()

    def log_message(self, *a):
        pass


class _Picture(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Length", "3")
        self.end_headers()
        self.wfile.write(b"jpg")

    def log_message(self, *a):
        pass


def _serve(handler):
    server = HTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def _allow_loopback_port(monkeypatch, port, extra=()):
    """Let the test server's port through as if it were public."""
    real = safe_fetch.resolve_public

    def fake(host, p):
        if p == port:
            return [*extra, (socket.AF_INET, ("127.0.0.1", port))]
        return real(host, p)

    monkeypatch.setattr(safe_fetch, "resolve_public", fake)


def test_redirect_to_lan_is_blocked(monkeypatch):
    server, port = _serve(_Redirector)
    _allow_loopback_port(monkeypatch, port)
    try:
        with pytest.raises(BlockedURL):
            fetch_public(f"http://127.0.0.1:{port}/thumb.jpg", 1024, timeout=5)
    finally:
        server.shutdown()


def test_connects_to_the_checked_address_ipv4_first(monkeypatch):
    server, port = _serve(_Picture)
    resolved = []
    real_connect = safe_fetch._connect

    def spy(host, p, timeout):
        sock = real_connect(host, p, timeout)
        resolved.append(sock.getpeername()[0])
        return sock

    _allow_loopback_port(monkeypatch, port)
    monkeypatch.setattr(safe_fetch, "_connect", spy)
    try:
        assert fetch_public(f"http://thumbs.example.test:{port}/a.jpg", 1024, timeout=5) == b"jpg"
    finally:
        server.shutdown()
    assert resolved == ["127.0.0.1"]


def test_unreachable_ipv6_falls_back_quickly(monkeypatch):
    import time
    server, port = _serve(_Picture)
    real = safe_fetch.resolve_public

    def fake(host, p):
        if p == port:
            return [(socket.AF_INET6, ("100::1", port, 0, 0)),
                    (socket.AF_INET, ("127.0.0.1", port))]
        return real(host, p)

    monkeypatch.setattr(safe_fetch, "resolve_public", fake)
    started = time.monotonic()
    sock = None
    try:
        sock = safe_fetch._connect("x.test", port, 15)
        assert sock.getpeername()[0] == "127.0.0.1"
    finally:
        # The test server answers one connection at a time: close ours
        # or shutdown() waits forever on a request that never comes.
        if sock:
            sock.close()
        server.shutdown()
    assert time.monotonic() - started < safe_fetch.CONNECT_TIMEOUT + 2


def test_resolver_orders_ipv4_first(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [
        (socket.AF_INET6, socket.SOCK_STREAM, 6, "", ("2606:4700:4700::1111", 443, 0, 0)),
        (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443)),
    ])
    assert [a[0] for a in safe_fetch.resolve_public("x.test", 443)] == [
        socket.AF_INET, socket.AF_INET6]


def test_thumbnail_service_refuses_lan_url(tmp_path):
    from app.services.thumbnails import ThumbnailService
    assert ThumbnailService(tmp_path).download("v1", "http://192.168.200.1/x.jpg") is None


def test_connected_socket_is_freed_when_the_caller_drops_it(monkeypatch):
    import gc
    import weakref
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    port = listener.getsockname()[1]
    monkeypatch.setattr(safe_fetch, "resolve_public", lambda host, p: [
        (socket.AF_INET6, ("100::1", port, 0, 0)), (socket.AF_INET, ("127.0.0.1", port))])
    monkeypatch.setattr(safe_fetch, "CONNECT_TIMEOUT", 0.5)
    gc.disable()
    try:
        ref = weakref.ref(safe_fetch._connect("x.test", port, 5))
        assert ref() is None
    finally:
        gc.enable()
        listener.close()
