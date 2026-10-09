from __future__ import annotations
"""Run ffmpeg as a job that can be watched and cancelled.

ffmpeg is started in its own process group with ``-progress pipe:1``.
A reader thread keeps only the latest progress numbers; the calling
thread polls them, works out a rolling speed and time left, and on a
cancel or a stall terminates the whole group (SIGTERM, then SIGKILL)
and reaps it. ffmpeg's stderr goes to a file, never a pipe, so a chatty
encoder can neither block on a full pipe nor grow this process's
memory.
"""

import os
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

STALL_SECONDS = 120.0
_SPEED_WINDOW = 60.0
_POLL = 0.5
_TERM_GRACE = 5.0


class Cancelled(Exception):
    """The job was cancelled; partial output must be discarded."""


class Stalled(Exception):
    """ffmpeg stopped making progress and was stopped."""


class CancelToken:
    """Thread-safe flag a worker checks between steps."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def check(self) -> None:
        if self._event.is_set():
            raise Cancelled()


@dataclass
class Progress:
    """A snapshot of one phase of a job."""
    phase: str
    done: float = 0.0
    total: float = 0.0
    speed: float | None = None
    """Media seconds per wall second, measured (convert phase only)."""
    eta_seconds: float | None = None
    stalled_for: float = 0.0

    @property
    def fraction(self) -> float | None:
        if self.total <= 0:
            return None
        return max(0.0, min(1.0, self.done / self.total))


ProgressCallback = Callable[[Progress], None]


class RateEstimator:
    """Rolling rate over the last ``window`` seconds of wall time."""

    def __init__(self, window: float = _SPEED_WINDOW, clock=time.monotonic):
        self.window = window
        self.clock = clock
        self.samples: deque[tuple[float, float]] = deque()

    def add(self, done: float) -> None:
        now = self.clock()
        self.samples.append((now, done))
        while len(self.samples) > 2 and now - self.samples[0][0] > self.window:
            self.samples.popleft()

    def rate(self) -> float | None:
        if len(self.samples) < 2:
            return None
        (t0, d0), (t1, d1) = self.samples[0], self.samples[-1]
        if t1 - t0 <= 0 or d1 < d0:
            return None
        return (d1 - d0) / (t1 - t0)

    def eta(self, done: float, total: float) -> float | None:
        rate = self.rate()
        if not rate or total <= 0:
            return None
        return max(0.0, (total - done) / rate)


class _ProgressReader(threading.Thread):
    """Parses ``-progress`` key=value lines; remembers only the latest."""

    def __init__(self, stream):
        super().__init__(daemon=True)
        self.stream = stream
        self.lock = threading.Lock()
        self.out_seconds = 0.0
        self.changed_at = time.monotonic()
        self.ended = False

    def run(self) -> None:
        for line in self.stream:
            key, _, value = line.strip().partition("=")
            if key in ("out_time_us", "out_time_ms") and value.isdigit():
                seconds = int(value) / 1_000_000
                with self.lock:
                    if seconds > self.out_seconds:
                        self.out_seconds = seconds
                        self.changed_at = time.monotonic()
            elif key == "progress" and value == "end":
                with self.lock:
                    self.ended = True

    def snapshot(self) -> tuple[float, float]:
        with self.lock:
            return self.out_seconds, self.changed_at


def _stop_group(proc: subprocess.Popen) -> None:
    for sig, grace in ((signal.SIGTERM, _TERM_GRACE), (signal.SIGKILL, None)):
        try:
            os.killpg(proc.pid, sig)
        except ProcessLookupError:
            break
        try:
            proc.wait(timeout=grace)
            break
        except subprocess.TimeoutExpired:
            continue
    proc.wait()


def run_ffmpeg(args: list[str], *, duration: float, log_path: Path,
               on_progress: ProgressCallback | None = None,
               cancel: CancelToken | None = None,
               stall_seconds: float = STALL_SECONDS,
               phase: str = "convert") -> int:
    """Run ``args`` (starting with the ffmpeg binary) to completion.

    Returns ffmpeg's exit code. Raises Cancelled or Stalled after the
    process group has been stopped and reaped; the caller deletes any
    partial output. ``duration`` is the input length in seconds, used
    for the fraction and the time left.
    """
    if cancel is not None:
        cancel.check()
    cmd = [args[0], "-nostats", "-progress", "pipe:1", *args[1:]]
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "ab") as log:
        proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=log, start_new_session=True, text=True,
                                errors="replace")
    reader = _ProgressReader(proc.stdout)
    reader.start()
    estimator = RateEstimator()
    try:
        while True:
            code = proc.poll()
            done, changed_at = reader.snapshot()
            estimator.add(done)
            stalled_for = time.monotonic() - changed_at
            if on_progress is not None:
                on_progress(Progress(phase=phase, done=done, total=duration,
                                     speed=estimator.rate(),
                                     eta_seconds=estimator.eta(done, duration),
                                     stalled_for=stalled_for))
            if code is not None:
                return code
            if cancel is not None and cancel.cancelled:
                _stop_group(proc)
                raise Cancelled()
            if stalled_for > stall_seconds:
                _stop_group(proc)
                raise Stalled(f"no progress for {int(stalled_for)} s")
            time.sleep(_POLL)
    finally:
        if proc.poll() is None:
            _stop_group(proc)
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        reader.join(timeout=5)
        if proc.stdout is not None and not reader.is_alive():
            proc.stdout.close()


def tail_log(log_path: Path, limit: int = 400) -> str:
    """The last ``limit`` characters of an ffmpeg log, for error messages."""
    try:
        with open(log_path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - limit))
            return f.read().decode("utf-8", "replace").strip()
    except OSError:
        return ""
