"""Restarting things, and the Pi's power and temperature readings."""

from __future__ import annotations

import threading
import time

from syshelper import runner
from syshelper.registry import action

THROTTLE_BITS = {
    0: "under_voltage_now", 1: "freq_capped_now", 2: "throttled_now", 3: "soft_temp_limit_now",
    16: "under_voltage_seen", 17: "freq_capped_seen", 18: "throttled_seen", 19: "soft_temp_limit_seen",
}


def later(argv: list[str], delay: float = 1.5) -> None:
    """Run argv after the reply has gone out (restarting the web server
    or the box would otherwise cut the reply off)."""
    def go() -> None:
        time.sleep(delay)
        runner.run(argv, timeout=120)
    threading.Thread(target=go, daemon=True).start()


@action("restart_curatables")
def restart_curatables(params: dict) -> dict:
    later(["systemctl", "restart", "curatables.service"])
    return {"restarting": True}


@action("reboot")
def reboot(params: dict) -> dict:
    later(["systemctl", "reboot"], delay=2)
    return {"rebooting": True}


@action("restart_network")
def restart_network(params: dict) -> dict:
    later(["systemctl", "restart", "NetworkManager.service"])
    return {"restarting": True}


def power() -> dict:
    """Under-voltage and throttling flags from the firmware, plus the
    temperature. Empty when vcgencmd is missing (not a Raspberry Pi)."""
    out: dict = {}
    throttled = runner.run(["vcgencmd", "get_throttled"])
    if throttled.ok and "=" in throttled.out:
        try:
            value = int(throttled.out.strip().split("=", 1)[1], 16)
        except ValueError:
            value = None
        if value is not None:
            out["throttled_raw"] = hex(value)
            for bit, name in THROTTLE_BITS.items():
                out[name] = bool(value >> bit & 1)
    temp = runner.run(["vcgencmd", "measure_temp"])
    if temp.ok and "=" in temp.out:
        try:
            out["temp_c"] = float(temp.out.split("=", 1)[1].split("'")[0])
        except ValueError:
            pass
    return out


@action("power_status")
def power_status(params: dict) -> dict:
    return power()
