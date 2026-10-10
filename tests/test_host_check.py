"""The server answers only to its own names and IP addresses (DNS rebinding)."""

import socket

import pytest
from fastapi.testclient import TestClient

from app.middleware.host_check import allowed_names, host_allowed


@pytest.mark.parametrize("host", [
    "curatables.local", "curatables.local:80", "CURATABLES.LOCAL.", "curatables", "curatables.lan",
    "curatables.home.arpa", "192.0.2.16", "192.0.2.16:8080", "[::1]", "[fe80::1]:80", "localhost:8080",
    f"{socket.gethostname().split('.')[0]}.local",
])
def test_the_box_names_and_addresses_are_allowed(host):
    assert host_allowed(host, allowed_names())


@pytest.mark.parametrize("host", ["evil.example", "curatables.evil.example", "curatables.local.evil.example",
                                  "", "192.0.2.16.nip.io"])
def test_other_names_are_refused(host):
    assert not host_allowed(host, allowed_names())


def test_extra_names_from_settings():
    assert host_allowed("kids.example.org", allowed_names(["kids.example.org"]))


def test_a_rebound_request_is_refused_before_setup(app):
    app.state.config.parent.password_hash = None
    c = TestClient(app, base_url="http://attacker.example", follow_redirects=False)
    resp = c.post("/parent/setup", data={"password": "takeover1", "password2": "takeover1"})
    assert resp.status_code == 400 and "curatables.local" in resp.text
    assert app.state.config.is_first_run


def test_the_normal_name_works(app):
    c = TestClient(app, base_url="http://curatables.local")
    assert c.get("/healthz").status_code == 200
