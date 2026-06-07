from __future__ import annotations
"""Simple in-memory sliding-window rate limiter.

One module-level instance is enough for a single-tenant family server.
If we ever scale horizontally, this has to move to a shared store
(Redis, a DB table with an index), but that's premature for now.
"""

import threading
import time


class RateLimitExceeded(Exception):
    """Raised by CommentService.post when the caller is over quota."""


class RateLimiter:
    def __init__(self, max_events: int, window_seconds: float):
        self.max_events = max_events
        self.window = window_seconds
        self._times: dict[object, list[float]] = {}
        self._lock = threading.Lock()

    def check(self, key) -> bool:
        """Return True if the event is allowed; record it. Return False
        if the key has already hit max_events within the window (no
        record is made in that case)."""
        now = time.monotonic()
        with self._lock:
            times = self._times.setdefault(key, [])
            # Drop entries outside the window
            cutoff = now - self.window
            while times and times[0] < cutoff:
                times.pop(0)
            if len(times) >= self.max_events:
                return False
            times.append(now)
            return True

    def reset(self, key=None) -> None:
        """Clear one key (or all keys if None). Used by tests."""
        with self._lock:
            if key is None:
                self._times.clear()
            else:
                self._times.pop(key, None)
