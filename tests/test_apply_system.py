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
