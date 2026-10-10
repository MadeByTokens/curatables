"""Answer only to the box's own names and to IP addresses.

A web page on another site can make a browser on the home network talk
to this server through its own domain name (DNS rebinding); the browser
then treats the server as part of that site and lets the page read the
answers. Such requests carry the other site's name in the Host header,
so refusing unknown names stops them.
"""

from __future__ import annotations

import ipaddress
import os
import socket

from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Suffixes home routers add to device names in their own DNS.
LOCAL_SUFFIXES = ("", ".local", ".lan", ".home", ".localdomain", ".home.arpa", ".internal")


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        return False
    return True


def allowed_names(extra: list[str] | tuple[str, ...] = ()) -> set[str]:
    names = {"localhost"}
    for base in {socket.gethostname().split(".")[0].lower(), "curatables"}:
        names.update(base + suffix for suffix in LOCAL_SUFFIXES)
    names.update(h.strip().lower() for h in extra if h.strip())
    names.update(h.strip().lower() for h in os.environ.get("CURATABLES_ALLOWED_HOSTS", "").split(",")
                 if h.strip())
    return names


def host_allowed(host_header: str, names: set[str]) -> bool:
    host = host_header.strip().lower()
    if host.startswith("["):
        host = host[: host.find("]") + 1] if "]" in host else host
    elif host.count(":") == 1:
        host = host.split(":", 1)[0]
    host = host.rstrip(".")
    return bool(host) and (_is_ip(host) or host in names)


class HostCheckMiddleware:
    def __init__(self, app: ASGIApp, extra_hosts=lambda: ()):
        self.app = app
        self.extra_hosts = extra_hosts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        host = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"host"), "")
        if not host_allowed(host, allowed_names(tuple(self.extra_hosts()))):
            resp = PlainTextResponse(
                "This address is not one Curatables answers to. Open http://curatables.local/ "
                "or the box's number address instead.", status_code=400)
            await resp(scope, receive, send)
            return
        await self.app(scope, receive, send)
