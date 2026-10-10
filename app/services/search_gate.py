"""Every YouTube search from the kid pages goes through one gate, so a
child tapping fast, or several children at once, can't make the box look
like a bot.

- Answers are cached in their own small database (cache.db, safe to
  delete): a video's details for a week, searches and channel listings
  for CACHE_SECONDS. Refresh, Back, sorting, the next page, a restart,
  another kid and the parent's Add preview all reuse them.
- One YouTube call at a time for the whole box.
- Each kid has a tap limit (every search page, cached or not) and a
  YouTube-call limit; the box has its own call budget.
- When YouTube answers with a bot check or "too many requests", searching
  rests for REST_SECONDS.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from app.backends.base import BackendError, BackendErrorType, ChannelHit, VideoMetadata
from app.services.rate_limit import RateLimiter

CACHE_SECONDS = 6 * 3600
VIDEO_CACHE_SECONDS = 7 * 86400
CACHE_ENTRIES = 5000
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


_TYPES: dict[str, type] = {"video": VideoMetadata, "channel": ChannelHit}


class _Cache:
    """(key → items) with an expiry, in SQLite: a file that survives
    restarts, or memory when there is none (tests)."""

    def __init__(self, path: Path | None = None):
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path) if path else ":memory:", check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS answers (key TEXT PRIMARY KEY, expires REAL NOT NULL,"
            " asked INTEGER NOT NULL, items TEXT NOT NULL)")
        self._lock = threading.Lock()

    def get(self, key: tuple) -> tuple[int, list] | None:
        with self._lock:
            row = self._db.execute("SELECT asked, items FROM answers WHERE key = ? AND expires > ?",
                                   (json.dumps(key), time.time())).fetchone()
        if row is None:
            return None
        return row[0], [_TYPES[t](**d) for t, d in json.loads(row[1])]

    def put(self, key: tuple, asked: int, items: list) -> None:
        ttl = VIDEO_CACHE_SECONDS if key[0] == "video" else CACHE_SECONDS
        packed = json.dumps([["video" if isinstance(i, VideoMetadata) else "channel", asdict(i)]
                             for i in items])
        with self._lock, self._db:
            self._db.execute("INSERT OR REPLACE INTO answers VALUES (?, ?, ?, ?)",
                             (json.dumps(key), time.time() + ttl, asked, packed))
            self._db.execute("DELETE FROM answers WHERE expires <= ?", (time.time(),))
            self._db.execute(
                "DELETE FROM answers WHERE key NOT IN "
                "(SELECT key FROM answers ORDER BY expires DESC LIMIT ?)", (CACHE_ENTRIES,))


@dataclass
class GateStatus:
    resting_until: float
    failures: int
    last_failure: str


class SearchGate:
    def __init__(self, cache_path: Path | None = None) -> None:
        self.taps = RateLimiter(max_events=8, window_seconds=30)
        self.kid_calls = RateLimiter(max_events=12, window_seconds=300)
        self.box_calls = RateLimiter(max_events=30, window_seconds=600)
        self._cache = _Cache(cache_path)
        self._youtube = threading.Lock()
        self.resting_until = 0.0
        self._failures: list[tuple[float, str]] = []

    def tap(self, kid: int) -> None:
        if not self.taps.check(kid):
            raise SearchRefused("slow")

    def use_file(self, path: Path) -> None:
        self._cache = _Cache(path)

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
            self._cache.put(key, want, items)
            return items
        finally:
            self._youtube.release()

    def video(self, raw_id: str, call: Callable[[], VideoMetadata | None]) -> VideoMetadata | None:
        """A YouTube video's details for the parent: from the cache when a
        kid or the parent looked at it this week, else from YouTube (one
        call at a time, no kid limits). A failure is the caller's to show."""
        key = ("video", raw_id)
        hit = self._cached(key, 1)
        if hit and isinstance(hit[0], VideoMetadata):
            return hit[0]
        with self._youtube:
            hit = self._cached(key, 1)
            if hit and isinstance(hit[0], VideoMetadata):
                return hit[0]
            meta = call()
            if meta is not None:
                self._cache.put(key, 1, [meta])
            return meta

    def _cached(self, key: tuple, want: int) -> list[Any] | None:
        found = self._cache.get(key)
        if found is None:
            return None
        asked, items = found
        # Fewer items than asked for means YouTube has no more.
        if asked >= want or len(items) < asked:
            return items
        return None

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
