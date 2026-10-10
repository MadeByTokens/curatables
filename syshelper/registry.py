"""The fixed list of things the helper will do, and parameter checks."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from syshelper.errors import HelperError


@dataclass
class Action:
    name: str
    fn: Callable[..., Any]
    job: bool
    title: str


ACTIONS: dict[str, Action] = {}


def action(name: str, *, job: bool = False, title: str = ""):
    """Register fn as action `name`. A job action runs in the background
    and receives the Job as its second argument."""
    def decorate(fn):
        ACTIONS[name] = Action(name, fn, job, title or name)
        return fn
    return decorate


def text(params: dict, key: str, *, max_len: int = 256, required: bool = True) -> str:
    value = params.get(key, "")
    if not isinstance(value, str):
        raise HelperError(f"'{key}' must be text")
    if any(c in value for c in "\0\n\r"):
        raise HelperError(f"'{key}' cannot contain line breaks")
    if len(value) > max_len:
        raise HelperError(f"'{key}' is too long")
    if required and not value:
        raise HelperError(f"'{key}' is missing")
    return value


def flag(params: dict, key: str, default: bool = False) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise HelperError(f"'{key}' must be true or false")
    return value
