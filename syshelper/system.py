"""Restarting things, the clock, and the Pi's power and temperature readings."""

from __future__ import annotations

import threading
import time

from syshelper import runner
from syshelper.errors import HelperError
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


@action("shutdown")
def shutdown(params: dict) -> dict:
    """Power off cleanly: services stop and every drive is unmounted
    before the power goes, so unplugging afterwards is safe."""
    later(["systemctl", "poweroff"], delay=2)
    return {"shutting_down": True}


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


# The Pi has no battery-backed clock: after a power cut it starts from the
# time fake-hwclock saved, and stays there until NTP reaches the internet.
# A phone or tablet opening a page knows the real time, so the app hands
# it over. Only forward, and never by more than this: the saved time is
# always behind, and nobody can push the clock years ahead.
MAX_CLOCK_STEP = 400 * 86400
MIN_CLOCK_STEP = 120


def ntp_synchronized() -> bool:
    result = runner.run(["timedatectl", "show", "-p", "NTPSynchronized", "--value"])
    return result.ok and result.out.strip() == "yes"


@action("clock_status")
def clock_status(params: dict) -> dict:
    return {"synchronized": ntp_synchronized(), "now": time.time()}


@action("set_clock")
def set_clock(params: dict) -> dict:
    """Step the clock forward to `epoch` (seconds) unless NTP already set it."""
    epoch = params.get("epoch")
    if not isinstance(epoch, int) or isinstance(epoch, bool):
        raise HelperError("'epoch' must be a whole number of seconds")
    if ntp_synchronized():
        return {"set": False, "reason": "synchronized"}
    step = epoch - time.time()
    if step < MIN_CLOCK_STEP:
        return {"set": False, "reason": "not ahead"}
    if step > MAX_CLOCK_STEP:
        raise HelperError("That time is too far ahead of the box's clock.")
    result = runner.run(["date", "-u", "-s", f"@{epoch}"])
    if not result.ok:
        raise HelperError(f"Could not set the clock: {result.err.strip() or result.out.strip()}")
    # Save it, so the next boot after a power cut starts from here.
    runner.run(["fake-hwclock", "save"])
    return {"set": True, "step": int(step)}
