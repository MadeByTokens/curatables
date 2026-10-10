"""mDNS / Zeroconf advertisement of the server as ``_http._tcp.local.``.

Rules:
- Never crash the app: a missing library, failed IP detection or a
  daemon error is logged and the server boots anyway.
- Async only: the sync ``Zeroconf`` class refuses to run inside the
  uvicorn event loop, so ``start()``/``stop()`` use ``AsyncZeroconf``.
- ``stop()`` is idempotent and safe after a failed or missing ``start()``.
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

# Factory sentinel: "use the real zeroconf class". Passing None instead
# means "pretend the library isn't installed" (used by tests).
_USE_DEFAULT = object()


def detect_lan_ip() -> str:
    """Return the host's primary LAN IPv4 address, or ``127.0.0.1``.

    Connecting a UDP socket sends nothing; it just makes the kernel pick
    the source address it would use to reach a public host.
    """
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 53))
        return str(s.getsockname()[0])
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def _slugify(name: str) -> str:
    """Hostname label for ``<slug>.local.``: lower-case alphanumerics,
    with each run of anything else collapsed to one dash."""
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
            # Stored before anything can raise so _safe_close() can
            # shut it down instead of leaking the listener.
            self._azc = azc
            # Auto-suffix ("Curatables (2)") on a LAN name collision
            # rather than failing.
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
            logger.debug("mDNS close failed", exc_info=True)
        self._azc = None
        self._info = None
        self._started = False
