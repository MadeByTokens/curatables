"""Every YouTube search from the kid pages goes through one gate, so a
child tapping fast, or several children at once, can't make the box look
like a bot.

- Results are cached for CACHE_SECONDS: refresh, Back, sorting and the
  next page reuse them.
- One YouTube call at a time for the whole box.
- Each kid has a tap limit (every search page, cached or not) and a
  YouTube-call limit; the box has its own call budget.
- When YouTube answers with a bot check or "too many requests", searching
  rests for REST_SECONDS.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable

from app.backends.base import BackendError, BackendErrorType
from app.services.rate_limit import RateLimiter

CACHE_SECONDS = 15 * 60
CACHE_ENTRIES = 128
REST_SECONDS = 30 * 60
LOCK_WAIT_SECONDS = 25
FAILURE_WINDOW_SECONDS = 3600


class SearchRefused(Exception):
    """Why a search didn't run. kind: 'slow' (this kid is tapping too
    fast), 'busy' (the box's budget is used up or another search is
    taking long), 'resting' (YouTube pushed back) or 'broken' (the
    search failed)."""

    def __init__(self, kind: str):
        super().__init__(kind)
        self.kind = kind


@dataclass
class _Entry:
    expires: float
    asked: int
    items: list


@dataclass
class GateStatus:
    resting_until: float
    failures: int
    last_failure: str


class SearchGate:
    def __init__(self) -> None:
        self.taps = RateLimiter(max_events=8, window_seconds=30)
        self.kid_calls = RateLimiter(max_events=12, window_seconds=300)
        self.box_calls = RateLimiter(max_events=30, window_seconds=600)
        self._cache: OrderedDict[tuple, _Entry] = OrderedDict()
        self._cache_lock = threading.Lock()
        self._youtube = threading.Lock()
        self.resting_until = 0.0
        self._failures: list[tuple[float, str]] = []

    def tap(self, kid: int) -> None:
        if not self.taps.check(kid):
            raise SearchRefused("slow")

    def fetch(self, key: tuple, want: int, kid: int,
              call: Callable[[int], tuple[list[Any], BackendError | None]]) -> list[Any]:
        """At least `want` items for `key` (fewer when that's all there
        is). `call(n)` asks YouTube for n items and returns them with the
        backend's error, if any."""
        hit = self._cached(key, want)
        if hit is not None:
            return hit
        if time.time() < self.resting_until:
            raise SearchRefused("resting")
        if not self._youtube.acquire(timeout=LOCK_WAIT_SECONDS):
            raise SearchRefused("busy")
        try:
            hit = self._cached(key, want)
            if hit is not None:
                return hit
            if time.time() < self.resting_until:
                raise SearchRefused("resting")
            if not self.kid_calls.check(kid):
                raise SearchRefused("slow")
            if not self.box_calls.check("box"):
                raise SearchRefused("busy")
            items, error = call(want)
            if error is not None and not items:
                self._failed(error)
            self._store(key, want, items)
            return items
        finally:
            self._youtube.release()

    def _cached(self, key: tuple, want: int) -> list[Any] | None:
        with self._cache_lock:
            entry = self._cache.get(key)
            if entry is None or entry.expires < time.time():
                return None
            # Fewer items than asked for means YouTube has no more.
            if entry.asked >= want or len(entry.items) < entry.asked:
                self._cache.move_to_end(key)
                return entry.items
            return None

    def _store(self, key: tuple, asked: int, items: list[Any]) -> None:
        with self._cache_lock:
            self._cache[key] = _Entry(time.time() + CACHE_SECONDS, asked, list(items))
            self._cache.move_to_end(key)
            while len(self._cache) > CACHE_ENTRIES:
                self._cache.popitem(last=False)

    def _failed(self, error: BackendError) -> None:
        now = time.time()
        self._failures = [f for f in self._failures if f[0] > now - FAILURE_WINDOW_SECONDS]
        self._failures.append((now, error.message))
        if error.error_type == BackendErrorType.BOT_BLOCKED:
            self.resting_until = now + REST_SECONDS
            raise SearchRefused("resting")
        raise SearchRefused("broken")

    def status(self) -> GateStatus:
        now = time.time()
        recent = [f for f in self._failures if f[0] > now - FAILURE_WINDOW_SECONDS]
        return GateStatus(self.resting_until if self.resting_until > now else 0.0,
                          len(recent), recent[-1][1] if recent else "")

    def reset(self) -> None:
        """Tests: forget everything."""
        self.__init__()  # type: ignore[misc]


GATE = SearchGate()
