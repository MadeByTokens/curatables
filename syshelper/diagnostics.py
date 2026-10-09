"""Health checks with plain-language fixes, and a diagnostics report
that is safe to send to someone helping (passwords and keys removed)."""

from __future__ import annotations

import json
import os
import re
import shutil
import socket
import time
from pathlib import Path

from syshelper import drives, nm, runner, system
from syshelper.registry import action
from syshelper.settings import SETTINGS

OK, WARN, FAIL = "ok", "warn", "fail"
GB = 1024 ** 3


def _check(cid: str, title: str, status: str, detail: str = "", fix: str = "") -> dict:
    return {"id": cid, "title": title, "status": status, "detail": detail, "fix": fix}


def _tcp(host: str, port: int, timeout: float = 5) -> str:
    """'' when a TCP connection works, else why it did not."""
    try:
        infos = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except OSError:
        return "dns"
    for *_rest, addr in infos[:3]:
        try:
            with socket.create_connection((str(addr[0]), int(addr[1])), timeout=timeout):
                return ""
        except OSError:
            continue
    return "connect"


def check_network() -> list[dict]:
    out = []
    devs = nm.devices()
    wired = any(d["type"] == "ethernet" and d["state"] == "connected" for d in devs)
    wifi = next((d for d in devs if d["type"] == "wifi"), None)
    wifi_up = bool(wifi and wifi["state"] == "connected")
    if wired:
        out.append(_check("network", "Network connection", OK, "Connected by Ethernet cable."))
    elif wifi and wifi_up:
        signal = 0
        for r in nm.rows("-f", "ACTIVE,SIGNAL", "device", "wifi", "list", "--rescan", "no"):
            if len(r) >= 2 and r[0] == "yes" and r[1].isdigit():
                signal = int(r[1])
        status = OK if signal >= 40 or signal == 0 else WARN
        out.append(_check("network", "Network connection", status,
                          f"Connected by Wi-Fi ({wifi['connection']}), signal {signal}%.",
                          "" if status == OK else "The Wi-Fi signal is weak. Move the box closer to the "
                          "router, or use an Ethernet cable."))
    else:
        out.append(_check("network", "Network connection", FAIL, "Not connected to any network.",
                          "Plug in an Ethernet cable, or set up Wi-Fi again with the curatables-wifi.txt "
                          "file on the SD card."))
        return out

    dns = _tcp("www.youtube.com", 443)
    if dns == "":
        out.append(_check("internet", "Internet and YouTube", OK, "YouTube can be reached."))
    elif dns == "dns":
        out.append(_check("internet", "Internet and YouTube", FAIL, "Names cannot be looked up (DNS).",
                          "Restart the router. If it keeps happening, check the router's internet "
                          "connection."))
    else:
        reach = _tcp("one.one.one.one", 443)
        out.append(_check("internet", "Internet and YouTube", FAIL if reach else WARN,
                          "No internet connection." if reach else "The internet works but YouTube "
                          "cannot be reached.",
                          "Check that other devices can use the internet, then restart the router."
                          if reach else "YouTube may be blocked by the router or down. Try again later."))
    return out


def check_clock() -> dict:
    synced = runner.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"]).out.strip()
    year = time.gmtime().tm_year
    if year < 2025:
        return _check("clock", "Clock", FAIL, f"The clock says {year}.",
                      "Secure downloads fail with a wrong clock. Connect the box to the internet; "
                      "it sets the time by itself within a few minutes.")
    if synced == "no":
        return _check("clock", "Clock", WARN, "The clock has not been checked against the internet yet.",
                      "This fixes itself once the box has internet for a few minutes.")
    return _check("clock", "Clock", OK, time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime()))


def _active(unit: str) -> bool:
    return runner.run(["systemctl", "is-active", unit]).out.strip() == "active"


def check_services() -> list[dict]:
    out = []
    if _active("curatables.service"):
        out.append(_check("server", "Curatables server", OK, "Running."))
    else:
        out.append(_check("server", "Curatables server", FAIL, "Not running.",
                          "Press Restart Curatables. If that does not help, restart the box."))
    names_ok = _active("avahi-daemon.service")
    helper_ok = _active("curatables-mdns-unicast.service")
    if names_ok and helper_ok:
        out.append(_check("name", "curatables.local name", OK, "Announced on the network."))
    else:
        out.append(_check("name", "curatables.local name", WARN, "The name is not being announced.",
                          "Use the box's address (shown at the top of this page) instead, or restart "
                          "the box."))
    failed = runner.run(["systemctl", "--failed", "--plain", "--no-legend"]).out.split("\n")
    failed = [line.split()[0] for line in failed if line.strip()]
    if failed:
        out.append(_check("units", "System services", WARN, "Not running: " + ", ".join(failed[:6]),
                          "Restart the box. If this stays, send the diagnostics report to whoever helps you."))
    return out


def check_storage() -> list[dict]:
    out = []
    try:
        sd = shutil.disk_usage(SETTINGS.data_dir if SETTINGS.data_dir.exists() else "/")
        free = sd.free / GB
        status = OK if free >= 4 else WARN if free >= 1 else FAIL
        out.append(_check("sd", "SD card space", status, f"{free:.1f} GB free of {sd.total / GB:.0f} GB.",
                          "" if status == OK else "Delete videos you no longer need, or put the videos "
                          "on a USB drive (Drives page)."))
    except OSError:
        pass
    media = drives.media_state()
    if media["configured"]:
        if media["mounted"]:
            free = media["free"] / GB
            status = OK if free >= 4 else WARN
            out.append(_check("drive", "Videos drive", status, f"Connected, {free:.1f} GB free.",
                              "" if status == OK else "The videos drive is nearly full."))
        elif media["present"]:
            out.append(_check("drive", "Videos drive", FAIL, "Plugged in but not opened.",
                              "Press Reconnect on the Drives page. If that fails, press Repair."))
        else:
            out.append(_check("drive", "Videos drive", FAIL, "Not plugged in.",
                              "Plug the videos drive back in and press Reconnect on the Drives page."))
    return out


def check_power() -> list[dict]:
    p = system.power()
    if not p:
        return []
    out = []
    if p.get("under_voltage_now") or p.get("under_voltage_seen"):
        out.append(_check("power", "Power supply", FAIL if p.get("under_voltage_now") else WARN,
                          "The box is not getting enough power" + (" right now." if p.get("under_voltage_now")
                                                                    else " (it happened since start-up)."),
                          "Use the official Raspberry Pi power supply. A USB hard drive may need a powered "
                          "USB hub or its own power adapter. Low power causes drives to disconnect and "
                          "files to get damaged."))
    else:
        out.append(_check("power", "Power supply", OK, "Enough power."))
    temp = p.get("temp_c")
    if temp is not None:
        hot = temp >= 80 or p.get("throttled_now")
        out.append(_check("temp", "Temperature", WARN if hot else OK, f"{temp:.0f} °C.",
                          "The box is very hot and slows itself down. Give it air, or add a case with a "
                          "fan or heatsink." if hot else ""))
    return out


def recent_errors() -> list[str]:
    result = runner.run(["journalctl", "-b", "-u", "curatables.service", "-p", "err", "-n", "15",
                         "--no-pager", "-o", "short-iso"], timeout=30)
    return [line for line in result.out.splitlines() if line and not line.startswith("--")]


@action("diagnostics")
def diagnostics(params: dict) -> dict:
    checks: list[dict] = []
    checks += check_network()
    checks.append(check_clock())
    checks += check_services()
    checks += check_storage()
    checks += check_power()
    return {"checks": checks, "errors": [redact(e, secrets()) for e in recent_errors()]}


# --- report -------------------------------------------------------------------

def secrets() -> set[str]:
    """Literal values that must never appear in a report."""
    found: set[str] = set()
    for path in Path("/etc/NetworkManager/system-connections").glob("*"):
        try:
            for line in path.read_text(errors="replace").splitlines():
                key, _, value = line.partition("=")
                if key.strip() in ("psk", "password", "wep-key0", "private-key-password") and value.strip():
                    found.add(value.strip())
        except OSError:
            pass
    try:
        cfg = json.loads((SETTINGS.data_dir / "config.json").read_text())
        for section, key in (("parent", "password_hash"), ("parent", "session_secret"),
                             ("imports", "tmdb_api_key")):
            secret = (cfg.get(section) or {}).get(key)
            if isinstance(secret, str) and secret:
                found.add(secret)
    except (OSError, ValueError):
        pass
    return {s for s in found if len(s) >= 4}


PATTERNS = [
    (re.compile(r"(?i)\b(psk|password|passwd|secret|token|api[_-]?key)(\s*[=:]\s*)\S+"), r"\1\2[hidden]"),
    (re.compile(r"\$(?:1|5|6|y|2[aby]|gy|7)\$[^\s:]+"), "[hidden-hash]"),
    (re.compile(r"pbkdf2[$:][^\s\"']+"), "[hidden-hash]"),
]


def redact(text: str, literal: set[str]) -> str:
    for value in sorted(literal, key=len, reverse=True):
        text = text.replace(value, "[hidden]")
    for pattern, repl in PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _section(title: str, argv: list[str], timeout: float = 30) -> str:
    result = runner.run(argv, timeout=timeout)
    body = (result.out + ("\n" + result.err if result.err.strip() else "")).strip()
    return f"\n===== {title} =====\n{body or '(nothing)'}\n"


def _file(title: str, path: Path, tail: int = 200) -> str:
    try:
        lines = path.read_text(errors="replace").splitlines()[-tail:]
    except OSError:
        return f"\n===== {title} =====\n(not found)\n"
    return f"\n===== {title} =====\n" + "\n".join(lines) + "\n"


def _version() -> str:
    try:
        for line in (SETTINGS.code_dir / "app" / "__init__.py").read_text().splitlines():
            if line.startswith("__version__"):
                return line.split("=", 1)[1].strip().strip("\"'")
    except OSError:
        pass
    return "unknown"


def build_report() -> str:
    parts = [
        "Curatables diagnostics report\n",
        f"Created: {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}\n",
        f"Curatables version: {_version()}\n",
        "Passwords, Wi-Fi passwords and keys have been removed. It does contain network names\n"
        "and local addresses: share it only with someone you trust to help.\n",
    ]
    try:
        parts.append("Model: " + Path("/proc/device-tree/model").read_text().strip("\0\n") + "\n")
    except OSError:
        pass
    try:
        pretty: str = next((line.split("=", 1)[1].strip('"') for line in
                       Path("/etc/os-release").read_text().splitlines() if line.startswith("PRETTY_NAME=")), "")
        parts.append(f"System: {pretty}\n")
    except OSError:
        pass
    parts.append(_section("Uptime", ["uptime"]))
    try:
        result = diagnostics({})
        lines = [f"[{c['status'].upper():4}] {c['title']}: {c['detail']}"
                 + (f"\n       Fix: {c['fix']}" if c["fix"] and c["status"] != OK else "")
                 for c in result["checks"]]
        parts.append("\n===== Checks =====\n" + "\n".join(lines) + "\n")
    except Exception as e:
        parts.append(f"\n===== Checks =====\n(could not run: {type(e).__name__}: {e})\n")
    parts.append(_section("Power (vcgencmd)", ["vcgencmd", "get_throttled"]))
    parts.append(_section("Network devices", ["nmcli", "device"]))
    parts.append(_section("Saved networks", ["nmcli", "-f", "NAME,TYPE,AUTOCONNECT,DEVICE", "connection", "show"]))
    parts.append(_section("Addresses", ["ip", "-brief", "address"]))
    parts.append(_section("Routes", ["ip", "route"]))
    parts.append(_section("Drives", ["lsblk", "-o", "NAME,SIZE,TYPE,FSTYPE,LABEL,MOUNTPOINT,TRAN,MODEL"]))
    parts.append(_section("Space", ["df", "-h", "-x", "tmpfs", "-x", "devtmpfs"]))
    parts.append(_file("/etc/fstab", SETTINGS.fstab_path))
    parts.append(_section("Services", ["systemctl", "--no-pager", "status", "curatables.service",
                                       "curatables-helper.service", "curatables-mdns-unicast.service",
                                       "avahi-daemon.service", "NetworkManager.service"]))
    parts.append(_section("Failed services", ["systemctl", "--failed", "--no-pager"]))
    parts.append(_file("First-boot log (SD card)", SETTINGS.boot_dir / "curatables-firstboot.log"))
    parts.append(_section("Curatables log (this boot)", ["journalctl", "-b", "-u", "curatables.service",
                                                         "-n", "300", "--no-pager", "-o", "short-iso"]))
    parts.append(_section("System log (this boot)", ["journalctl", "-b", "-n", "300", "--no-pager",
                                                     "-o", "short-iso", "-u", "NetworkManager.service",
                                                     "-u", "curatables-helper.service",
                                                     "-u", "curatables-firstboot.service"]))
    parts.append(_section("Kernel messages", ["journalctl", "-k", "-b", "-n", "150", "--no-pager",
                                              "-o", "short-iso"]))
    app_log = SETTINGS.data_dir / "logs" / "curatables.log"
    if app_log.exists():
        parts.append(_file("Curatables app log", app_log, tail=300))
    return redact("".join(parts), secrets())


@action("diagnostics_report")
def diagnostics_report(params: dict) -> dict:
    return {"text": build_report()}


def write_text_file(path: str, content: str) -> None:
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    with open(tmp, "w", newline="\r\n") as f:
        f.write(content)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)
