"""scripts/nm-wired-only.sh keeps Wi-Fi off while Ethernet is connected."""

import os
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "nm-wired-only.sh"

FAKE_NMCLI = r"""#!/bin/sh
echo "$@" >> "$FAKE_LOG"
case "$*" in
  "-t -f TYPE,STATE device")
    echo "ethernet:$FAKE_ETH_STATE"
    echo "wifi:$FAKE_WIFI_STATE"
    echo "loopback:connected (externally)" ;;
  "-t -f DEVICE,TYPE device")
    echo "eth0:ethernet"
    echo "wlan0:wifi"
    echo "lo:loopback" ;;
  "-g GENERAL.TYPE device show eth0") echo ethernet ;;
  "-g GENERAL.TYPE device show wlan0") echo wifi ;;
  "-g GENERAL.TYPE device show lo") echo loopback ;;
esac
exit 0
"""


def _run(tmp_path, iface, action, *, eth="connected", wifi="connected"):
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    log.write_text("")
    (bindir / "nmcli").write_text(FAKE_NMCLI)
    (bindir / "logger").write_text(f'#!/bin/sh\necho logger "$@" >> "{log}"\n')
    for stub in bindir.iterdir():
        stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}", FAKE_LOG=str(log),
               FAKE_ETH_STATE=eth, FAKE_WIFI_STATE=wifi)
    proc = subprocess.run(["sh", str(SCRIPT), iface, action], env=env,
                          capture_output=True, text=True, timeout=10)
    assert proc.returncode == 0, proc.stderr
    time.sleep(0.2)
    return [line for line in log.read_text().splitlines()
            if not line.startswith("-t ") and not line.startswith("-g ")]


def test_cable_plugged_in_turns_wifi_off(tmp_path):
    calls = _run(tmp_path, "eth0", "up")
    assert calls[:2] == ["device set wlan0 autoconnect no", "device disconnect wlan0"]
    assert any("Wi-Fi turned off" in c for c in calls)


def test_cable_unplugged_brings_wifi_back(tmp_path):
    calls = _run(tmp_path, "eth0", "down", eth="unavailable", wifi="disconnected")
    assert "device set wlan0 autoconnect yes" in calls
    assert "device connect wlan0" in calls


def test_wifi_coming_up_while_wired_is_turned_off_again(tmp_path):
    calls = _run(tmp_path, "wlan0", "up")
    assert "device disconnect wlan0" in calls


def test_wifi_coming_up_without_a_cable_is_left_alone(tmp_path):
    assert _run(tmp_path, "wlan0", "up", eth="unavailable") == []


@pytest.mark.parametrize("iface,action", [("wlan0", "down"), ("lo", "up"),
                                          ("eth0", "dhcp4-change"), ("", "up")])
def test_other_events_change_nothing(tmp_path, iface, action):
    assert _run(tmp_path, iface, action, eth="unavailable", wifi="disconnected") == []
