"""Run a program and capture what it says. Tests replace `run`."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass


@dataclass
class Result:
    rc: int
    out: str = ""
    err: str = ""

    @property
    def ok(self) -> bool:
        return self.rc == 0

    @property
    def text(self) -> str:
        return (self.err or self.out).strip()


ENV = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"}


def run(argv: list[str], *, input: str | None = None, timeout: float = 60) -> Result:
    try:
        proc = subprocess.run(argv, input=input, capture_output=True, text=True,
                              timeout=timeout, env=dict(ENV, HOME=os.environ.get("HOME", "/root")))
    except FileNotFoundError:
        return Result(127, "", f"{argv[0]} is not installed")
    except subprocess.TimeoutExpired:
        return Result(124, "", f"{argv[0]} did not finish within {int(timeout)} seconds")
    return Result(proc.returncode, proc.stdout, proc.stderr)


def stream(argv: list[str], on_line, *, timeout: float = 4 * 3600, env: dict | None = None) -> int:
    """Run argv, passing each line of its output (stdout and stderr
    together) to on_line as it appears. Returns the exit code."""
    import threading
    try:
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                env=dict(ENV, HOME=os.environ.get("HOME", "/root"), **(env or {})))
    except FileNotFoundError:
        on_line(f"{argv[0]} is not installed")
        return 127
    timer = threading.Timer(timeout, proc.kill)
    timer.start()
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                on_line(line)
        return proc.wait()
    finally:
        timer.cancel()
