"""scripts/firstboot.sh applies Wi-Fi from the boot partition and then
removes the password from that file."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "firstboot.sh"

WIFI = (
    "# Curatables Wi-Fi setup\r\n"
    "#  - psk:     your Wi-Fi password\r\n"
    "\r\n"
    "country=GB\r\n"
    "ssid=Home Net\r\n"
    "psk=hunter2-secret\r\n"
)


FAKE_NMCLI = r"""#!/bin/sh
echo "$@" >> "$FAKE_LOG"
case "$*" in
  "-t -f DEVICE,TYPE device")
    [ -n "$FAKE_IFACE" ] && echo "$FAKE_IFACE:wifi"
    echo "eth0:ethernet" ;;
  *"device wifi list"*)
    echo "Neighbour"
    [ "$FAKE_VISIBLE" = 1 ] && echo "$FAKE_SSID" ;;
  "connection add"*) exit "$FAKE_ADD_EXIT" ;;
  *"802-11-wireless.hidden yes"*) touch "$FAKE_LOG.hidden" ;;
  *"802-11-wireless.hidden no"*) rm -f "$FAKE_LOG.hidden" ;;
  *"connection up"*)
    if [ "$FAKE_VISIBLE" != 1 ]; then
      [ "$FAKE_HIDDEN" = 1 ] && [ -e "$FAKE_LOG.hidden" ] && exit 0
      echo "Error: No network with SSID found."
      exit 10
    fi
    [ "$FAKE_UP_EXIT" = 0 ] || echo "Error: Secrets were required, but not provided."
    exit "$FAKE_UP_EXIT" ;;
esac
exit 0
"""


def _run(tmp_path, wifi_text, *, iface="wlan0", visible=True, up_exit=0, add_exit=0,
         ssid="Home Net", hidden=False):
    wifi = tmp_path / "curatables-wifi.txt"
    wifi.write_text(wifi_text, newline="")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "calls.log"
    (bindir / "raspi-config").write_text(f'#!/bin/sh\necho raspi-config "$@" >> "{log}"\n')
    (bindir / "nmcli").write_text(FAKE_NMCLI)
    (bindir / "sleep").write_text("#!/bin/sh\n")
    for stub in bindir.iterdir():
        stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               CURATABLES_WIFI_FILE=str(wifi), CURATABLES_WIFI_WAIT="20",
               CURATABLES_WIFI_STEP="5", FAKE_LOG=str(log), FAKE_IFACE=iface,
               FAKE_VISIBLE="1" if visible else "0", FAKE_UP_EXIT=str(up_exit),
               FAKE_ADD_EXIT=str(add_exit), FAKE_SSID=ssid,
               FAKE_HIDDEN="1" if hidden else "0")
    proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=30)
    boot_log = tmp_path / "curatables-firstboot.log"
    proc.boot_log = boot_log.read_text() if boot_log.exists() else ""
    return proc, wifi.read_text(), log.read_text() if log.exists() else ""


def test_saves_a_profile_connects_then_blanks_ssid_and_password(tmp_path):
    proc, after, calls = _run(tmp_path, WIFI)
    assert proc.returncode == 0, proc.stderr
    assert "raspi-config nonint do_wifi_country GB" in calls
    assert ("connection add type wifi con-name curatables-wifi ifname wlan0 "
            "ssid Home Net connection.autoconnect yes "
            "wifi-sec.key-mgmt wpa-psk wifi-sec.psk hunter2-secret") in calls
    assert "connection up curatables-wifi" in calls
    assert "hunter2-secret" not in after
    assert "\nssid=\n" in after and "\npsk=\n" in after
    assert "country=GB" in after
    assert after.startswith("# Wi-Fi was set up on ")
    assert "your Wi-Fi password" in after
    assert "connected to 'Home Net'" in proc.boot_log
    assert "hunter2-secret" not in proc.boot_log


def test_next_boot_leaves_the_saved_network_alone(tmp_path):
    _run(tmp_path, WIFI)
    wifi = tmp_path / "curatables-wifi.txt"
    proc, after, calls = _run(tmp_path, wifi.read_text())
    assert proc.returncode == 0
    assert calls.count("connection add") == 1
    assert after.count("# Wi-Fi was set up on ") == 1


def test_network_not_in_range_keeps_the_file_and_says_why(tmp_path):
    proc, after, calls = _run(tmp_path, WIFI, visible=False)
    assert proc.returncode == 0
    assert "psk=hunter2-secret" in after
    assert "connection add" in calls
    assert "802-11-wireless.hidden yes" in calls
    assert calls.rstrip().endswith("802-11-wireless.hidden no")
    assert "'Home Net' was not found" in proc.boot_log


def test_hidden_network_connects_after_the_scan_never_shows_it(tmp_path):
    proc, after, calls = _run(tmp_path, WIFI, visible=False, hidden=True)
    assert proc.returncode == 0
    assert "connection modify curatables-wifi 802-11-wireless.hidden yes" in calls
    assert "connected to hidden network 'Home Net'" in proc.boot_log
    assert "hunter2-secret" not in after


def test_wrong_password_keeps_the_file_and_hides_it_in_the_log(tmp_path):
    proc, after, calls = _run(tmp_path, WIFI, up_exit=4)
    assert proc.returncode == 0
    assert "psk=hunter2-secret" in after
    assert "Check the password" in proc.boot_log
    assert "Secrets were required" in proc.boot_log
    assert "hunter2-secret" not in proc.boot_log


def test_waits_for_the_wifi_device_then_gives_up(tmp_path):
    proc, after, calls = _run(tmp_path, WIFI, iface="")
    assert proc.returncode == 0
    assert "connection add" not in calls
    assert "no Wi-Fi device appeared" in proc.boot_log
    assert "psk=hunter2-secret" in after


def test_failed_save_keeps_the_file(tmp_path):
    proc, after, calls = _run(tmp_path, WIFI, add_exit=1)
    assert proc.returncode == 0
    assert "could not save the network" in proc.boot_log
    assert "psk=hunter2-secret" in after


def test_open_network_without_psk_line(tmp_path):
    proc, after, calls = _run(tmp_path, "country=US\nssid=Cafe\n", ssid="Cafe")
    assert proc.returncode == 0, proc.stderr
    assert "ssid Cafe connection.autoconnect yes\n" in calls
    assert "wifi-sec" not in calls


def test_ssid_with_glob_characters_matches_exactly(tmp_path):
    text = "country=GB\nssid=Home*Net[1]\npsk=a*b?c\n"
    proc, after, calls = _run(tmp_path, text, ssid="Home*Net[1]", up_exit=4)
    assert "Check the password" in proc.boot_log
    assert "a*b?c" not in proc.boot_log


@pytest.mark.parametrize("text", ["", "country=US\nssid=\npsk=\n"])
def test_nothing_to_do_without_ssid(tmp_path, text):
    proc, after, calls = _run(tmp_path, text)
    assert proc.returncode == 0
    assert calls == ""
    assert after == text
