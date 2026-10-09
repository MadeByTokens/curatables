"""Small request helpers shared by the routers."""

from fastapi import Request
from starlette.datastructures import FormData


def is_xhr(request: Request) -> bool:
    """True for requests made by the pages' XMLHttpRequest code, which
    expect a fragment or JSON instead of a redirect."""
    return request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"


def form_str(form: FormData, key: str, default: str = "") -> str:
    """A text field from a submitted form; missing keys and file parts
    give `default`."""
    value = form.get(key)
    return value if isinstance(value, str) else default


def kid_address(request: Request) -> str:
    """The kid address for a tablet: the host the parent's browser used,
    except that a loopback host becomes the server's LAN name."""
    from app.services.mdns import _slugify, detect_lan_ip
    url = request.url
    host = url.hostname or ""
    if host in ("localhost", "::1") or host.startswith("127."):
        server = request.app.state.config.server
        host = (f"{_slugify(server.mdns_name)}.local" if server.mdns_enabled
                else detect_lan_ip())
    port = f":{url.port}" if url.port else ""
    return f"{url.scheme}://{host}{port}/"
