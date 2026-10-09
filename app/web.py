"""Small request helpers shared by the routers."""

from fastapi import Request


def is_xhr(request: Request) -> bool:
    """True for requests made by the pages' XMLHttpRequest code, which
    expect a fragment or JSON instead of a redirect."""
    return request.headers.get("x-requested-with", "").lower() == "xmlhttprequest"
