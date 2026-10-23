"""scripts/apply-system.sh installs the image's system files from a code tree."""

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "apply-system.sh"


def test_installs_every_unit_the_tree_ships_and_the_hooks(tmp_path):
    env = dict(os.environ, CURATABLES_ETC=str(tmp_path), CURATABLES_SKIP_SYSTEMCTL="1")
    proc = subprocess.run(["bash", str(SCRIPT), str(ROOT)], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0, proc.stderr
    installed = {p.name for p in (tmp_path / "systemd" / "system").iterdir()}
    shipped = {p.name for p in (ROOT / "systemd").iterdir() if p.suffix in (".service", ".timer", ".path")}
    assert installed == shipped
    hook = tmp_path / "NetworkManager" / "dispatcher.d" / "70-curatables-wired-only"
    assert os.access(hook, os.X_OK)
    assert 'Unattended-Upgrade "1"' in (tmp_path / "apt" / "apt.conf.d" / "20auto-upgrades").read_text()
    unattended = (tmp_path / "apt" / "apt.conf.d" / "52curatables-unattended-upgrades").read_text()
    assert '"origin=Raspberry Pi Foundation,codename=${distro_codename}";' in unattended
    assert 'Automatic-Reboot "true"' in unattended and 'Automatic-Reboot-Time "04:00"' in unattended
    # Sorts after Raspberry Pi OS's 40-rpi-volatile-storage.conf, so it wins.
    journald = (tmp_path / "systemd" / "journald.conf.d" / "60-curatables.conf").read_text()
    assert "Storage=persistent" in journald and "SystemMaxUse=" in journald


def test_every_installed_unit_with_an_install_section_is_enabled():
    text = SCRIPT.read_text()
    enabled = text.split("ENABLE=(", 1)[1].split(")", 1)[0].split()
    for unit in (ROOT / "systemd").iterdir():
        if "[Install]" in unit.read_text() and unit.name != "curatables-updater.service" \
                and unit.name != "curatables-backup.service":
            assert unit.name in enabled, unit.name


def test_refuses_a_tree_without_units(tmp_path):
    proc = subprocess.run(["bash", str(SCRIPT), str(tmp_path)], capture_output=True, text=True,
                          env=dict(os.environ, CURATABLES_ETC=str(tmp_path / "etc")), timeout=30)
    assert proc.returncode != 0 and "no systemd/ folder" in proc.stderr


def test_the_firewall_lets_in_what_the_box_is_for():
    """Dropping by default must never cost the web page, SSH, .local names
    (avahi and the unicast helper on 5353) or getting an address."""
    rules = (ROOT / "systemd" / "curatables-firewall.nft").read_text()
    assert "policy drop;" in rules
    for rule in ('iif "lo" accept', "ct state established,related accept", "tcp dport { 22, 80 } accept",
                 "udp dport 5353 accept", "udp sport 67 udp dport 68 accept",
                 "meta l4proto { icmp, ipv6-icmp } accept"):
        assert rule in rules
    assert "chain output" not in rules and "chain forward" not in rules  # nothing going out is blocked
    unit = (ROOT / "systemd" / "curatables-firewall.service").read_text()
    assert "/opt/curatables/systemd/curatables-firewall.nft" in unit
