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


def test_redirect_to_lan_is_blocked(monkeypatch):
    server = HTTPServer(("127.0.0.1", 0), _Redirector)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    port = server.server_address[1]
    real_check = safe_fetch.check_url
    calls = []

    def first_hop_allowed(url):
        calls.append(url)
        if len(calls) > 1:
            real_check(url)

    monkeypatch.setattr(safe_fetch, "check_url", first_hop_allowed)
    try:
        with pytest.raises(BlockedURL):
            fetch_public(f"http://127.0.0.1:{port}/thumb.jpg", 1024, timeout=5)
    finally:
        server.shutdown()
    assert calls[-1] == "http://127.0.0.1:1/secret"


def test_thumbnail_service_refuses_lan_url(tmp_path):
    from app.services.thumbnails import ThumbnailService
    assert ThumbnailService(tmp_path).download("v1", "http://192.168.200.1/x.jpg") is None
