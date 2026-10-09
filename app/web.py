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
