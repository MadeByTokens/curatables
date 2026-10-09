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


def _run(tmp_path, wifi_text, raspi_exit=0):
    wifi = tmp_path / "curatables-wifi.txt"
    wifi.write_text(wifi_text, newline="")
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    log = tmp_path / "raspi.log"
    stub = bindir / "raspi-config"
    stub.write_text(f'#!/bin/sh\necho "$@" >> "{log}"\nexit {raspi_exit}\n')
    stub.chmod(0o755)
    env = dict(os.environ, PATH=f"{bindir}:{os.environ['PATH']}",
               CURATABLES_WIFI_FILE=str(wifi))
    proc = subprocess.run(["bash", str(SCRIPT)], env=env,
                          capture_output=True, text=True, timeout=30)
    return proc, wifi.read_text(), log.read_text() if log.exists() else ""


def test_applies_wifi_then_blanks_ssid_and_password(tmp_path):
    proc, after, calls = _run(tmp_path, WIFI)
    assert proc.returncode == 0, proc.stderr
    assert "do_wifi_country GB" in calls
    assert "do_wifi_ssid_passphrase Home Net hunter2-secret" in calls
    assert "hunter2-secret" not in after
    assert "\nssid=\n" in after and "\npsk=\n" in after
    assert "country=GB" in after
    assert after.startswith("# Wi-Fi was set up on ")
    assert "your Wi-Fi password" in after


def test_next_boot_leaves_the_saved_network_alone(tmp_path):
    _run(tmp_path, WIFI)
    wifi = tmp_path / "curatables-wifi.txt"
    proc, after, calls = _run(tmp_path, wifi.read_text())
    assert proc.returncode == 0
    assert calls.count("do_wifi_ssid_passphrase") == 1
    assert after.count("# Wi-Fi was set up on ") == 1


def test_failed_setup_keeps_the_file_for_another_try(tmp_path):
    proc, after, _ = _run(tmp_path, WIFI, raspi_exit=1)
    assert proc.returncode == 0
    assert "psk=hunter2-secret" in after


def test_open_network_without_psk_line(tmp_path):
    proc, after, calls = _run(tmp_path, "country=US\nssid=Cafe\n")
    assert proc.returncode == 0, proc.stderr
    assert "do_wifi_ssid_passphrase Cafe " in calls


@pytest.mark.parametrize("text", ["", "country=US\nssid=\npsk=\n"])
def test_nothing_to_do_without_ssid(tmp_path, text):
    proc, after, calls = _run(tmp_path, text)
    assert proc.returncode == 0
    assert calls == ""
    assert after == text
