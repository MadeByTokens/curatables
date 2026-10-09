"""Wi-Fi: what is connected, what is in range, and switching networks
with an automatic return to the old one if the new one does not work."""

from __future__ import annotations

import re
import time
from typing import NoReturn

from syshelper import nm, runner
from syshelper.errors import HelperError
from syshelper.jobs import Job
from syshelper.registry import action, flag, text
from syshelper.settings import SETTINGS

PROFILE = "curatables-wifi"
TRIAL = "curatables-wifi-trial"
CONNECT_WAIT = 45


def _active_wifi(dev: str) -> tuple[str, int]:
    for r in nm.rows("-f", "ACTIVE,SSID,SIGNAL", "device", "wifi", "list", "ifname", dev, "--rescan", "no"):
        if len(r) >= 3 and r[0] == "yes":
            return r[1], int(r[2]) if r[2].isdigit() else 0
    return "", 0


def _country() -> str:
    result = runner.run(["raspi-config", "nonint", "get_wifi_country"])
    return result.out.strip() if result.ok else ""


@action("wifi_status")
def wifi_status(params: dict) -> dict:
    devs = nm.devices()
    wifi = next((d for d in devs if d["type"] == "wifi"), None)
    wired = next((d for d in devs if d["type"] == "ethernet"), None)
    status: dict = {
        "wired": bool(wired and wired["state"] == "connected"),
        "wired_addresses": nm.ipv4(wired["device"]) if wired and wired["state"] == "connected" else [],
        "wifi_device": wifi["device"] if wifi else "",
        "wifi_connected": bool(wifi and wifi["state"] == "connected"),
        "ssid": "", "signal": 0, "wifi_addresses": [],
        "saved_ssid": "", "country": _country(),
    }
    if wifi:
        if status["wifi_connected"]:
            status["ssid"], status["signal"] = _active_wifi(wifi["device"])
            status["wifi_addresses"] = nm.ipv4(wifi["device"])
        saved = runner.run(["nmcli", "-g", "802-11-wireless.ssid", "connection", "show", PROFILE])
        status["saved_ssid"] = saved.out.strip() if saved.ok else ""
    return status


@action("wifi_scan")
def wifi_scan(params: dict) -> dict:
    dev = nm.wifi_device()
    if not dev:
        raise HelperError("This box has no Wi-Fi.")
    best: dict[str, dict] = {}
    for r in nm.rows("-f", "SSID,SIGNAL,SECURITY", "device", "wifi", "list", "ifname", dev,
                     "--rescan", "yes", timeout=45):
        if len(r) < 3 or not r[0]:
            continue
        signal = int(r[1]) if r[1].isdigit() else 0
        if r[0] not in best or signal > best[r[0]]["signal"]:
            best[r[0]] = {"ssid": r[0], "signal": signal, "secured": r[2] not in ("", "--")}
    return {"networks": sorted(best.values(), key=lambda n: -n["signal"])}


def _validate(params: dict) -> tuple[str, str, bool, str]:
    ssid = text(params, "ssid", max_len=64)
    if not ssid.strip() or len(ssid.encode()) > 32:
        raise HelperError("A Wi-Fi name is 1 to 32 characters.")
    psk = text(params, "password", max_len=64, required=False)
    if psk and not (8 <= len(psk) <= 63 or re.fullmatch(r"[0-9A-Fa-f]{64}", psk)):
        raise HelperError("A Wi-Fi password is 8 to 63 characters (or leave it empty for an open network).")
    hidden = flag(params, "hidden")
    country = text(params, "country", max_len=2, required=False).upper()
    if country and not re.fullmatch(r"[A-Z]{2}", country):
        raise HelperError("The country is a two-letter code, like US, GB or CA.")
    return ssid, psk, hidden, country


def _redact(message: str, psk: str) -> str:
    return message.replace(psk, "***") if psk else message


def _wifi_off(dev: str) -> None:
    runner.run(["nmcli", "device", "set", dev, "autoconnect", "no"])
    runner.run(["nmcli", "device", "disconnect", dev])


def _connected_with_address(dev: str) -> bool:
    state = next((d for d in nm.devices() if d["device"] == dev), None)
    return bool(state and state["state"] == "connected" and nm.ipv4(dev))


@action("wifi_set", job=True, title="Switching Wi-Fi")
def wifi_set(params: dict):
    ssid, psk, hidden, country = _validate(params)
    dev = nm.wifi_device()
    if not dev:
        raise HelperError("This box has no Wi-Fi.")

    def work(job: Job) -> dict:
        previous = next((d["connection"] for d in nm.devices() if d["device"] == dev
                         and d["state"] == "connected"), "")
        wired = nm.wired_connected()
        SETTINGS.wifi_test_flag.touch()
        try:
            if country:
                job.log(f"Setting the Wi-Fi country to {country}")
                runner.run(["raspi-config", "nonint", "do_wifi_country", country], timeout=60)
            runner.run(["nmcli", "radio", "wifi", "on"])
            runner.run(["nmcli", "device", "set", dev, "autoconnect", "yes"])
            runner.run(["nmcli", "connection", "delete", TRIAL])
            add = ["nmcli", "connection", "add", "type", "wifi", "con-name", TRIAL, "ifname", dev,
                   "ssid", ssid, "connection.autoconnect", "no"]
            if psk:
                add += ["wifi-sec.key-mgmt", "wpa-psk", "wifi-sec.psk", psk]
            if hidden:
                add += ["802-11-wireless.hidden", "yes"]
            result = runner.run(add)
            if not result.ok:
                raise HelperError("The network could not be saved: " + _redact(result.text, psk))
            job.log(f"Trying '{ssid}' (up to {CONNECT_WAIT * 2} seconds)")
            reason = ""
            for attempt in range(2):
                runner.run(["nmcli", "device", "wifi", "rescan", "ifname", dev], timeout=20)
                up = runner.run(["nmcli", "--wait", str(CONNECT_WAIT), "connection", "up", TRIAL,
                                 "ifname", dev], timeout=CONNECT_WAIT + 15)
                if up.ok and _wait_for_address(dev):
                    break
                reason = _redact(up.text, psk)
                if attempt == 0:
                    job.log("The first try did not connect; trying once more")
            else:
                return _roll_back(job, dev, previous, ssid, reason)
            job.log(f"Connected to '{ssid}'")
            runner.run(["nmcli", "connection", "delete", PROFILE])
            runner.run(["nmcli", "connection", "modify", TRIAL, "connection.id", PROFILE,
                        "connection.autoconnect", "yes", "connection.autoconnect-priority", "10"])
            addresses = nm.ipv4(dev)
            if wired:
                job.log("Ethernet is plugged in, so Wi-Fi goes back to standby; "
                        "it is used when the cable is unplugged")
            return {"connected": True, "ssid": ssid, "addresses": addresses, "wired": wired}
        finally:
            if wired:
                _wifi_off(dev)
            SETTINGS.wifi_test_flag.unlink(missing_ok=True)

    return work


def _wait_for_address(dev: str, seconds: int = 20) -> bool:
    for _ in range(seconds):
        if _connected_with_address(dev):
            return True
        time.sleep(1)
    return False


def _roll_back(job: Job, dev: str, previous: str, ssid: str, reason: str) -> NoReturn:
    runner.run(["nmcli", "connection", "delete", TRIAL])
    if previous and previous != TRIAL:
        job.log(f"Going back to '{previous}'")
        runner.run(["nmcli", "--wait", str(CONNECT_WAIT), "connection", "up", previous],
                   timeout=CONNECT_WAIT + 15)
    if "secrets were required" in reason.lower() or "802-1x" in reason.lower():
        hint = "The password looks wrong."
    elif "no network with ssid" in reason.lower() or "not found" in reason.lower():
        hint = "The network was not found. Check the name (capitals matter) and that the box is in range."
    else:
        hint = reason or "The network did not answer."
    raise HelperError(f"Could not connect to '{ssid}'. {hint} Nothing was changed.")
