"""mDNS / Zeroconf advertisement.

Publishes the server as an `_http._tcp.local.` service so users on the
same LAN can discover it the way they discover a networked printer —
type `http://curatables.local/` in a browser and it just works.

Design rules:

1. **Never crash the app.** If `zeroconf` isn't installed, or LAN IP
   detection fails, or the Zeroconf daemon can't start, the rest of
   the server must still boot. Log a warning and carry on.

2. **Async-safe.** The advertiser runs inside the FastAPI lifespan,
   which lives on the uvicorn asyncio event loop. We use
   `zeroconf.asyncio.AsyncZeroconf` because the sync `Zeroconf` class
   refuses to run from inside a running event loop — recent versions
   raise `EventLoopBlocked` instead of silently freezing the loop.
   `start()` and `stop()` are therefore coroutines and must be
   awaited.

3. **Injectable for tests.** `AsyncZeroconf` talks to the network, so
   unit tests inject fake factories via the `azc_factory` /
   `service_info_factory` constructor arguments. Passing the literal
   `None` means "pretend the library isn't installed" and is how the
   library-missing branch is exercised in CI.

4. **Idempotent start/stop.** `stop()` must be safe to call multiple
   times (FastAPI lifespan can fire shutdown on SIGINT *and* on the
   normal exit path) and safe to call when `start()` was never called
   or failed.
"""

from __future__ import annotations

import logging
import socket
from typing import Callable

logger = logging.getLogger(__name__)

try:
    from zeroconf import ServiceInfo
    from zeroconf.asyncio import AsyncZeroconf
    _HAVE_ZEROCONF = True
except Exception:  # pragma: no cover - import guard
    ServiceInfo = None  # type: ignore[assignment,misc]
    AsyncZeroconf = None  # type: ignore[assignment,misc]
    _HAVE_ZEROCONF = False


SERVICE_TYPE = "_http._tcp.local."

# Sentinel that means "caller didn't override this factory, use the
# library default". Passing the literal `None` still works and means
# "pretend the library is unavailable" — used by unit tests to cover
# the install-missing branch without uninstalling zeroconf in CI.
_USE_DEFAULT = object()


def detect_lan_ip() -> str:
    """Return the host's primary LAN IPv4 address, or ``127.0.0.1``.

    Uses the classic UDP-connect trick: the kernel picks the source
    address it would use to reach a public address, without actually
    sending any packet. Works on hosts with multiple interfaces, falls
    back cleanly on hosts with no network.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _slugify(name: str) -> str:
    """Lower-case, strip dots and whitespace, keep alnum + dash.

    Used to build the hostname portion (`<slug>.local.`) of the mDNS
    service record. Runs of non-alphanumerics collapse into a single
    dash so `Weird___Name` becomes `weird-name`, not `weird---name`.
    The user-visible friendly name stays untouched.
    """
    out = []
    prev_dash = False
    for ch in name.lower().strip():
        if ch.isalnum():
            out.append(ch)
            prev_dash = False
        else:
            if not prev_dash:
                out.append("-")
                prev_dash = True
    slug = "".join(out).strip("-")
    return slug or "curatables"


class ZeroconfAdvertiser:
    """Advertise the server over mDNS for the lifetime of an app.

    Typical lifecycle (managed by the FastAPI lifespan):

        adv = ZeroconfAdvertiser(name="Curatables", port=8080)
        await adv.start()   # best-effort; never raises
        ...                  # app runs
        await adv.stop()     # idempotent
    """

    def __init__(self,
                 name: str,
                 port: int,
                 service_type: str = SERVICE_TYPE,
                 ip_detector: Callable[[], str] = detect_lan_ip,
                 azc_factory=_USE_DEFAULT,
                 service_info_factory=_USE_DEFAULT):
        self.name = name
        self.port = int(port)
        self.service_type = service_type
        self._ip_detector = ip_detector
        # Resolve factories. _USE_DEFAULT means "pick up whatever the
        # real zeroconf library exposes, or None if it isn't installed".
        # Passing the literal None means "explicitly disable" and is
        # what unit tests use to exercise the library-missing branch.
        if azc_factory is _USE_DEFAULT:
            azc_factory = AsyncZeroconf if _HAVE_ZEROCONF else None
        if service_info_factory is _USE_DEFAULT:
            service_info_factory = ServiceInfo if _HAVE_ZEROCONF else None
        self._azc_factory = azc_factory
        self._service_info_factory = service_info_factory
        self._azc = None
        self._info = None
        self._started = False

    @property
    def available(self) -> bool:
        """True if the zeroconf async API is importable and usable."""
        return self._azc_factory is not None and self._service_info_factory is not None

    @property
    def started(self) -> bool:
        return self._started

    async def start(self) -> bool:
        """Register the service. Returns True on success, False on any
        handled failure (library missing, network error, collision, ...).
        Never raises.
        """
        if self._started:
            return True
        if not self.available:
            logger.info(
                "mDNS advertisement skipped: 'zeroconf' library not installed "
                "(install with: pip install zeroconf)"
            )
            return False
        try:
            ip = self._ip_detector()
            slug = _slugify(self.name)
            fqdn = f"{self.name}.{self.service_type}"
            server = f"{slug}.local."
            info = self._service_info_factory(
                type_=self.service_type,
                name=fqdn,
                addresses=[socket.inet_aton(ip)],
                port=self.port,
                server=server,
                properties={"path": "/"},
            )
            azc = self._azc_factory()
            # Store the daemon on self before anything that can raise
            # touches it, so the except-branch can close it via
            # _safe_close() instead of leaking a hanging listener.
            self._azc = azc
            # allow_name_change=True lets zeroconf auto-suffix the service
            # name (e.g. "Curatables (2)") if another instance already
            # holds ours on the LAN. Prefer collision survival over
            # crashing on a duplicate.
            await azc.async_register_service(info, allow_name_change=True)
            self._info = info
            self._started = True
            logger.info(
                "mDNS advertisement registered: http://%s:%d/  (service: %s)",
                server.rstrip("."), self.port, info.name,
            )
            return True
        except Exception as e:
            logger.warning("mDNS advertisement failed (%s: %s); server will "
                           "still run, but clients will need to know the IP "
                           "or hostname directly.", type(e).__name__, e)
            # Clean up a half-initialized AsyncZeroconf instance
            await self._safe_close()
            return False

    async def stop(self) -> None:
        """Deregister and shut the Zeroconf daemon down. Idempotent."""
        if not self._started:
            await self._safe_close()
            return
        azc = self._azc
        info = self._info
        self._azc = None
        self._info = None
        self._started = False
        if azc is None:
            return
        try:
            if info is not None:
                await azc.async_unregister_service(info)
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("mDNS unregister failed: %s", e)
        try:
            await azc.async_close()
        except Exception as e:  # pragma: no cover - best effort
            logger.warning("mDNS close failed: %s", e)

    async def _safe_close(self) -> None:
        """Best-effort cleanup when start() partially succeeded."""
        if self._azc is None:
            return
        try:
            await self._azc.async_close()
        except Exception:
            pass
        self._azc = None
        self._info = None
        self._started = False
