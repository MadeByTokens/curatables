"""Small wrappers around nmcli."""

from __future__ import annotations

from syshelper import runner


def split_terse(line: str) -> list[str]:
    """Split one line of `nmcli -t -e yes` output (':' separates fields,
    '\\:' and '\\\\' are escaped)."""
    fields, cur, i = [], "", 0
    while i < len(line):
        c = line[i]
        if c == "\\" and i + 1 < len(line):
            cur += line[i + 1]
            i += 2
            continue
        if c == ":":
            fields.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    fields.append(cur)
    return fields


def rows(*args: str, timeout: float = 30) -> list[list[str]]:
    result = runner.run(["nmcli", "-t", "-e", "yes", *args], timeout=timeout)
    if not result.ok:
        return []
    return [split_terse(line) for line in result.out.splitlines() if line]


def devices() -> list[dict]:
    """[{device, type, state, connection}]"""
    return [{"device": r[0], "type": r[1], "state": r[2], "connection": r[3] if len(r) > 3 else ""}
            for r in rows("-f", "DEVICE,TYPE,STATE,CONNECTION", "device") if len(r) >= 3]


def wifi_device() -> str:
    return next((d["device"] for d in devices() if d["type"] == "wifi"), "")


def wired_connected() -> bool:
    return any(d["type"] == "ethernet" and d["state"].startswith("connected") for d in devices())


def ipv4(device: str) -> list[str]:
    result = runner.run(["nmcli", "-g", "IP4.ADDRESS", "device", "show", device])
    return [a.split("/")[0] for a in result.out.replace("|", "\n").split() if a] if result.ok else []
