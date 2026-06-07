"""Unit tests for the mDNS advertiser.

These tests use a fake AsyncZeroconf class injected through
`azc_factory` and `service_info_factory`, so they run without touching
the real network and without requiring the `zeroconf` package to be
installed (though CI does install it via requirements.txt).

`ZeroconfAdvertiser.start()` / `stop()` are async coroutines (they
drive `zeroconf.asyncio.AsyncZeroconf`), so the tests are async too
and rely on pytest-asyncio (already a dev dependency).
"""

from __future__ import annotations

import asyncio
import socket

import pytest

from app.services.mdns import (
    SERVICE_TYPE,
    ZeroconfAdvertiser,
    _slugify,
    detect_lan_ip,
)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------


class FakeServiceInfo:
    """Record the constructor args so tests can assert on them."""

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.name = kwargs["name"]
        self.type_ = kwargs["type_"]
        self.port = kwargs["port"]
        self.server = kwargs["server"]
        self.addresses = kwargs["addresses"]
        self.properties = kwargs["properties"]


class FakeAsyncZeroconf:
    """In-memory async stand-in for the real zeroconf.asyncio.AsyncZeroconf.

    Records every register/unregister/close call and can be primed to
    raise on any of them via the `fail_on_*` flags. All three async
    methods are proper coroutines so `await adv.start()` reaches them.
    """

    instances: list["FakeAsyncZeroconf"] = []

    def __init__(self, fail_on_register: Exception | None = None,
                 fail_on_unregister: Exception | None = None,
                 fail_on_close: Exception | None = None):
        self.fail_on_register = fail_on_register
        self.fail_on_unregister = fail_on_unregister
        self.fail_on_close = fail_on_close
        self.registered: list[tuple[FakeServiceInfo, bool]] = []
        self.unregistered: list[FakeServiceInfo] = []
        self.closed = False
        FakeAsyncZeroconf.instances.append(self)

    async def async_register_service(self, info, allow_name_change=False, **_):
        if self.fail_on_register:
            raise self.fail_on_register
        self.registered.append((info, allow_name_change))

    async def async_unregister_service(self, info):
        if self.fail_on_unregister:
            raise self.fail_on_unregister
        self.unregistered.append(info)

    async def async_close(self):
        if self.fail_on_close:
            raise self.fail_on_close
        self.closed = True


def _fake_ip_detector(ip: str = "192.0.2.17"):
    return lambda: ip


def _make_adv(**overrides) -> ZeroconfAdvertiser:
    """Build an advertiser wired to fakes, unless overridden."""
    FakeAsyncZeroconf.instances.clear()
    kwargs = dict(
        name="Curatables",
        port=8080,
        ip_detector=_fake_ip_detector(),
        azc_factory=FakeAsyncZeroconf,
        service_info_factory=FakeServiceInfo,
    )
    kwargs.update(overrides)
    return ZeroconfAdvertiser(**kwargs)


# ---------------------------------------------------------------------------
# _slugify
# ---------------------------------------------------------------------------


class TestSlugify:
    @pytest.mark.parametrize("raw,expected", [
        ("Curatables", "curatables"),
        ("My Videos", "my-videos"),
        ("curatables.local", "curatables-local"),
        ("Weird___Name", "weird-name"),
        ("  spaced  ", "spaced"),
        ("A!B?C", "a-b-c"),
        ("", "curatables"),
        ("---", "curatables"),
    ])
    def test_slugify_produces_lan_safe_hostname(self, raw, expected):
        assert _slugify(raw) == expected


# ---------------------------------------------------------------------------
# detect_lan_ip
# ---------------------------------------------------------------------------


class TestDetectLanIp:
    def test_returns_a_string_ip(self):
        # We can't assert the exact IP because it depends on the host,
        # but it must be a parseable dotted quad and safe to use.
        ip = detect_lan_ip()
        assert isinstance(ip, str)
        try:
            socket.inet_aton(ip)
        except OSError:
            pytest.fail(f"detect_lan_ip returned non-IPv4 string: {ip!r}")

    def test_fallback_to_loopback_on_socket_error(self, monkeypatch):
        class Boom:
            def __init__(self, *a, **kw): pass
            def connect(self, *a, **kw):
                raise OSError("no network")
            def getsockname(self):
                raise AssertionError("should not reach getsockname")
            def close(self): pass
        monkeypatch.setattr(socket, "socket", lambda *a, **kw: Boom())
        assert detect_lan_ip() == "127.0.0.1"


# ---------------------------------------------------------------------------
# ZeroconfAdvertiser — happy path
# ---------------------------------------------------------------------------


class TestAdvertiserHappyPath:
    @pytest.mark.asyncio
    async def test_start_registers_service_with_expected_fields(self):
        adv = _make_adv(name="Curatables", port=8080,
                        ip_detector=_fake_ip_detector("192.168.1.42"))
        assert await adv.start() is True
        assert adv.started is True
        assert len(FakeAsyncZeroconf.instances) == 1
        azc = FakeAsyncZeroconf.instances[0]
        assert len(azc.registered) == 1
        info, allow_name_change = azc.registered[0]
        assert allow_name_change is True
        assert info.type_ == SERVICE_TYPE
        assert info.name == f"Curatables.{SERVICE_TYPE}"
        assert info.server == "curatables.local."
        assert info.port == 8080
        assert info.addresses == [socket.inet_aton("192.168.1.42")]
        assert info.properties == {"path": "/"}

    @pytest.mark.asyncio
    async def test_custom_name_is_slugified_for_hostname(self):
        adv = _make_adv(name="Family Media Box")
        await adv.start()
        azc = FakeAsyncZeroconf.instances[0]
        info = azc.registered[0][0]
        assert info.name == f"Family Media Box.{SERVICE_TYPE}"
        assert info.server == "family-media-box.local."

    @pytest.mark.asyncio
    async def test_start_is_idempotent(self):
        adv = _make_adv()
        assert await adv.start() is True
        assert await adv.start() is True  # second call no-ops
        # Only one AsyncZeroconf instance, only one register call
        assert len(FakeAsyncZeroconf.instances) == 1
        assert len(FakeAsyncZeroconf.instances[0].registered) == 1

    @pytest.mark.asyncio
    async def test_stop_unregisters_and_closes(self):
        adv = _make_adv()
        await adv.start()
        azc = FakeAsyncZeroconf.instances[0]
        await adv.stop()
        assert len(azc.unregistered) == 1
        assert azc.closed is True
        assert adv.started is False

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self):
        adv = _make_adv()
        await adv.start()
        await adv.stop()
        await adv.stop()  # must not raise
        azc = FakeAsyncZeroconf.instances[0]
        assert len(azc.unregistered) == 1  # not called again

    @pytest.mark.asyncio
    async def test_stop_without_start_is_safe(self):
        adv = _make_adv()
        await adv.stop()  # must not raise and must not instantiate anything
        assert FakeAsyncZeroconf.instances == []


# ---------------------------------------------------------------------------
# ZeroconfAdvertiser — failure paths (server must keep running)
# ---------------------------------------------------------------------------


class TestAdvertiserFailurePaths:
    @pytest.mark.asyncio
    async def test_start_returns_false_when_zeroconf_library_missing(self):
        adv = _make_adv(azc_factory=None, service_info_factory=None)
        assert adv.available is False
        assert await adv.start() is False
        assert adv.started is False

    @pytest.mark.asyncio
    async def test_start_returns_false_on_register_error(self, caplog):
        caplog.set_level("WARNING")

        def factory():
            return FakeAsyncZeroconf(fail_on_register=OSError("address in use"))

        adv = _make_adv(azc_factory=factory)
        assert await adv.start() is False
        assert adv.started is False
        assert "mDNS advertisement failed" in caplog.text
        # The half-initialized daemon must have been closed so we don't
        # leak a hanging listener.
        assert FakeAsyncZeroconf.instances[0].closed is True

    @pytest.mark.asyncio
    async def test_start_returns_false_on_ip_detection_error(self):
        def blow_up():
            raise RuntimeError("no interfaces")
        adv = _make_adv(ip_detector=blow_up)
        assert await adv.start() is False
        # We never got far enough to instantiate a daemon.
        assert FakeAsyncZeroconf.instances == []

    @pytest.mark.asyncio
    async def test_stop_tolerates_unregister_failure(self, caplog):
        caplog.set_level("WARNING")

        def factory():
            return FakeAsyncZeroconf(fail_on_unregister=RuntimeError("boom"))

        adv = _make_adv(azc_factory=factory)
        await adv.start()
        await adv.stop()  # must not raise
        assert FakeAsyncZeroconf.instances[0].closed is True
        assert "unregister failed" in caplog.text

    @pytest.mark.asyncio
    async def test_stop_tolerates_close_failure(self, caplog):
        caplog.set_level("WARNING")

        def factory():
            return FakeAsyncZeroconf(fail_on_close=RuntimeError("boom"))

        adv = _make_adv(azc_factory=factory)
        await adv.start()
        await adv.stop()  # must not raise
        assert "close failed" in caplog.text


# ---------------------------------------------------------------------------
# App lifespan integration
# ---------------------------------------------------------------------------


class TestAppLifespan:
    """Verify the advertiser is wired into the FastAPI lifespan.

    We can't easily intercept the ZeroconfAdvertiser class from inside
    the lifespan without heavier monkeypatching, but we CAN assert that
    `app.state.mdns` is populated (or None when disabled) and that
    disabling the advertiser via config prevents it from starting.
    """

    def test_mdns_is_disabled_when_config_says_so(self, tmp_path):
        from app.main import create_app
        from app.services.auth import AuthService
        from fastapi.testclient import TestClient

        app = create_app(data_dir=str(tmp_path))
        app.state.config.server.mdns_enabled = False
        AuthService(app.state.config).set_password("pw")
        with TestClient(app) as c:
            # TestClient triggers lifespan on enter.
            assert app.state.mdns is None
            r = c.get("/parent/login")
            assert r.status_code == 200

    def test_mdns_wires_into_lifespan_and_stops_cleanly(self, tmp_path,
                                                        monkeypatch):
        """With mdns_enabled=True, the lifespan creates an advertiser,
        awaits start() on it, and awaits stop() on shutdown.
        """
        from app.main import create_app
        from app.services.auth import AuthService
        from app.services import mdns as mdns_module
        from fastapi.testclient import TestClient

        calls = {"start": 0, "stop": 0}

        class StubAdvertiser:
            def __init__(self, name, port):
                self.name = name
                self.port = port
                self.started = False

            async def start(self):
                calls["start"] += 1
                self.started = True
                return True

            async def stop(self):
                calls["stop"] += 1
                self.started = False

        monkeypatch.setattr(mdns_module, "ZeroconfAdvertiser", StubAdvertiser)

        app = create_app(data_dir=str(tmp_path))
        app.state.config.server.mdns_enabled = True
        app.state.config.server.mdns_name = "TestBox"
        app.state.config.server.port = 9999
        AuthService(app.state.config).set_password("pw")

        with TestClient(app) as c:
            # Lifespan start fired on context enter.
            assert calls["start"] == 1
            adv = app.state.mdns
            assert isinstance(adv, StubAdvertiser)
            assert adv.name == "TestBox"
            assert adv.port == 9999
            r = c.get("/parent/login")
            assert r.status_code == 200

        # Lifespan stop fired on context exit.
        assert calls["stop"] == 1
