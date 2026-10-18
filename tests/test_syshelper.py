"""syshelper: the root helper's request handling, accounts, Wi-Fi,
drives and diagnostics, with every system command faked."""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from syshelper import accounts, diagnostics, drives, runner, server, system, wifi  # noqa: E402
from syshelper.errors import HelperError  # noqa: E402
from syshelper.registry import ACTIONS, action  # noqa: E402
from syshelper.runner import Result  # noqa: E402
from syshelper.settings import SETTINGS  # noqa: E402


class FakeRunner:
    """Answers commands by prefix; the most recently added rule wins."""

    def __init__(self):
        self.calls = []
        self.rules = []

    def on(self, prefix, rc=0, out="", err="", fn=None):
        self.rules.append((list(prefix), fn or Result(rc, out, err)))

    def run(self, argv, *, input=None, timeout=60):
        self.calls.append((list(argv), input))
        for prefix, res in reversed(self.rules):
            if argv[:len(prefix)] == prefix:
                return res(argv) if callable(res) else res
        return Result(0, "", "")

    def stream(self, argv, on_line, *, timeout=0, env=None):
        result = self.run(argv)
        for line in result.out.splitlines():
            on_line(line)
        return result.rc

    def argvs(self):
        return [c[0] for c in self.calls]

    def ran(self, *prefix):
        return any(a[:len(prefix)] == list(prefix) for a in self.argvs())


class FakeJob:
    def __init__(self):
        self.messages = []

    def log(self, message):
        self.messages.append(message)


@pytest.fixture
def fake(monkeypatch, tmp_path):
    f = FakeRunner()
    monkeypatch.setattr(runner, "run", f.run)
    monkeypatch.setattr(runner, "stream", f.stream)
    me = os.getuid()
    passwd = tmp_path / "passwd"
    passwd.write_text(
        "root:x:0:0:root:/root:/bin/bash\n"
        f"curatables:x:{me}:{os.getgid()}::/home/curatables:/usr/sbin/nologin\n"
        "admin:x:1000:1000::/home/admin:/usr/sbin/nologin\n"
        "later:x:1001:1001::/home/later:/bin/bash\n")
    shadow = tmp_path / "shadow"
    shadow.write_text("root:*:1::::::\nadmin:!:1::::::\n")
    fstab = tmp_path / "fstab"
    fstab.write_text("proc /proc proc defaults 0 0\n")
    data = tmp_path / "data"
    data.mkdir()
    for name, value in {
        "passwd_path": passwd, "shadow_path": shadow, "fstab_path": fstab, "data_dir": data,
        "service_user": "curatables", "admin_user": "",
        "media_mount": tmp_path / "media", "import_mount_root": tmp_path / "drives",
        "wifi_test_flag": tmp_path / "wifi-test", "socket_path": tmp_path / "run" / "helper.sock",
        "dropin_dir": tmp_path / "dropin", "boot_dir": tmp_path / "boot",
    }.items():
        monkeypatch.setattr(SETTINGS, name, value)
    monkeypatch.setattr(drives.pwd, "getpwnam", lambda name: type("P", (), {
        "pw_uid": me, "pw_gid": os.getgid()})())
    return f


# --- request handling ---------------------------------------------------------

def test_unknown_and_malformed_requests_are_refused():
    assert server.handle({"action": "rm -rf"}) == {"ok": False, "error": "unknown action 'rm -rf'"}
    assert not server.handle(None)["ok"]
    assert not server.handle({"action": "ping", "params": "x"})["ok"]
    assert server.handle({"action": "ping"}) == {"ok": True, "result": {"pong": True}}


def test_every_registered_action_is_one_we_know_about():
    assert set(ACTIONS) >= {
        "account_status", "account_password_matches", "account_set_password", "ssh_set",
        "wifi_status", "wifi_scan", "wifi_set", "restart_curatables", "reboot", "restart_network",
        "drives_list", "drive_use_for_videos", "drive_back_to_sd", "drive_eject",
        "drive_reconnect", "drive_repair", "drive_format", "drive_open_for_import",
        "drive_delete_sd_copy", "diagnostics", "diagnostics_report"}


def test_a_job_runs_in_the_background_and_reports_back():
    @action("_test_job", job=True, title="Testing")
    def _job(params):
        def work(job):
            job.log("step one")
            return {"answer": params["n"] * 2}
        return work

    try:
        started = server.handle({"action": "_test_job", "params": {"n": 21}})
        assert started["ok"] and started["result"]["title"] == "Testing"
        job_id = started["result"]["id"]
        for _ in range(100):
            status = server.handle({"action": "job_status", "params": {"id": job_id}})["result"]
            if status["state"] != "running":
                break
            import time
            time.sleep(0.02)
        assert status["state"] == "done"
        assert status["result"] == {"answer": 42}
        assert status["messages"] == ["step one"]
    finally:
        ACTIONS.pop("_test_job")


def test_a_failing_job_carries_the_parent_facing_message():
    @action("_test_fail", job=True)
    def _job(params):
        def work(job):
            raise HelperError("The drive is full.")
        return work

    try:
        job_id = server.handle({"action": "_test_fail"})["result"]["id"]
        import time
        for _ in range(100):
            status = server.handle({"action": "job_status", "params": {"id": job_id}})["result"]
            if status["state"] != "running":
                break
            time.sleep(0.02)
        assert status["state"] == "failed" and status["error"] == "The drive is full."
    finally:
        ACTIONS.pop("_test_fail")


def test_socket_answers_allowed_users_and_ignores_others():
    a, b = socket.socketpair()
    a.sendall(b'{"action": "ping"}\n')
    server._serve_one(b, {os.getuid()})
    assert json.loads(a.recv(4096)) == {"ok": True, "result": {"pong": True}}
    a.close()

    a, b = socket.socketpair()
    a.sendall(b'{"action": "ping"}\n')
    server._serve_one(b, {os.getuid() + 12345})
    try:
        assert a.recv(4096) == b""
    except ConnectionResetError:
        pass
    a.close()


# --- accounts -----------------------------------------------------------------

def test_admin_is_the_first_regular_account_other_than_the_service_user(fake):
    assert accounts.admin_user() == "admin"


def test_status_reports_a_locked_account(fake):
    fake.on(["systemctl", "is-enabled"], out="disabled\n")
    assert accounts.account_status({}) == {"user": "admin", "has_password": False, "ssh_enabled": False}


def _sha512(password):
    out = subprocess.run(["openssl", "passwd", "-6", "-salt", "saltsalt", password],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_password_check_against_the_shadow_hash(fake):
    SETTINGS.shadow_path.write_text(f"admin:{_sha512('correct horse')}:1::::::\n")
    assert accounts.account_password_matches({"password": "correct horse"}) == {"matches": True}
    assert accounts.account_password_matches({"password": "wrong horse"}) == {"matches": False}


def test_locked_account_never_matches(fake):
    assert not accounts.password_matches("anything", "!")
    assert not accounts.password_matches("anything", "")


def test_setting_the_password_uses_chpasswd_and_gives_a_shell(fake):
    assert accounts.account_set_password({"password": "a long password"}) == {"user": "admin"}
    assert (["chpasswd"], "admin:a long password\n") in fake.calls
    assert fake.ran("usermod", "-s", "/bin/bash", "admin")
    assert not fake.ran("cancel-rename")


def test_setting_the_password_closes_the_first_boot_user_dialog(fake):
    fake.on(["systemctl", "is-enabled", "userconfig.service"], out="enabled\n")
    accounts.account_set_password({"password": "a long password"})
    assert fake.ran("cancel-rename", "admin")
    assert fake.ran("systemctl", "stop", "userconfig.service")
    assert fake.ran("systemctl", "reset-failed", "userconfig.service")


@pytest.mark.parametrize("password", ["short", "two\nlines", "x" * 1025])
def test_bad_passwords_are_refused(fake, password):
    with pytest.raises(HelperError):
        accounts.account_set_password({"password": password})
    assert not fake.ran("chpasswd")


def test_chpasswd_errors_never_echo_the_password(fake):
    fake.on(["chpasswd"], rc=1, err="chpasswd: bad thing with a long password")
    with pytest.raises(HelperError) as e:
        accounts.account_set_password({"password": "a long password"})
    assert "a long password" not in str(e.value)


def test_ssh_off_disables_service_and_socket(fake):
    accounts.ssh_set({"enabled": False})
    assert fake.ran("systemctl", "disable", "--now", "ssh.service")
    assert fake.ran("systemctl", "disable", "--now", "ssh.socket")


def test_ssh_flag_must_be_a_boolean(fake):
    with pytest.raises(HelperError):
        accounts.ssh_set({"enabled": "yes"})


# --- Wi-Fi --------------------------------------------------------------------

DEVICES_WIFI = "eth0:ethernet:unavailable:\nwlan0:wifi:connected:Old Net\n"
DEVICES_WIRED = "eth0:ethernet:connected:Wired\nwlan0:wifi:disconnected:\n"


@pytest.fixture
def nofast(monkeypatch):
    monkeypatch.setattr(wifi.time, "sleep", lambda s: None)


@pytest.mark.parametrize("params", [
    {"ssid": ""}, {"ssid": "x" * 33}, {"ssid": "Home Net", "password": "short"},
    {"ssid": "Home Net", "country": "Canada"}, {"ssid": "Home Net", "hidden": "yes"},
])
def test_wifi_settings_are_checked_before_anything_runs(fake, params):
    with pytest.raises(HelperError):
        wifi.wifi_set(params)
    assert fake.calls == []


def test_wifi_switch_keeps_the_new_network_when_it_connects(fake, nofast):
    fake.on(["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], out=DEVICES_WIFI)
    fake.on(["nmcli", "-g", "IP4.ADDRESS"], out="192.0.2.30/24\n")
    work = wifi.wifi_set({"ssid": "Home Net", "password": "a*b?c secret", "country": "gb"})
    result = work(FakeJob())
    assert result["connected"] and result["ssid"] == "Home Net"
    assert fake.ran("raspi-config", "nonint", "do_wifi_country", "GB")
    add = next(a for a in fake.argvs() if a[:3] == ["nmcli", "connection", "add"])
    assert add[add.index("ssid") + 1] == "Home Net"
    assert add[add.index("wifi-sec.psk") + 1] == "a*b?c secret"
    assert fake.ran("nmcli", "connection", "delete", "curatables-wifi")
    assert fake.ran("nmcli", "connection", "modify", "curatables-wifi-trial", "connection.id", "curatables-wifi")
    assert not SETTINGS.wifi_test_flag.exists()


def test_wifi_switch_rolls_back_when_the_password_is_wrong(fake, nofast):
    fake.on(["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], out=DEVICES_WIFI)
    fake.on(["nmcli", "--wait", "45", "connection", "up", "curatables-wifi-trial"], rc=4,
            err="Error: Connection activation failed: Secrets were required, but not provided (a*b?c secret)")
    job = FakeJob()
    with pytest.raises(HelperError) as e:
        wifi.wifi_set({"ssid": "Home Net", "password": "a*b?c secret"})(job)
    assert "password looks wrong" in str(e.value) and "Nothing was changed" in str(e.value)
    assert "a*b?c secret" not in str(e.value)
    assert fake.ran("nmcli", "connection", "delete", "curatables-wifi-trial")
    assert fake.ran("nmcli", "--wait", "45", "connection", "up", "Old Net")
    assert not fake.ran("nmcli", "connection", "modify", "curatables-wifi-trial", "connection.id")
    assert not SETTINGS.wifi_test_flag.exists()


def test_wifi_tested_while_wired_goes_back_to_standby(fake, nofast):
    state = {"n": 0}

    def devices(argv):
        state["n"] += 1
        return Result(0, DEVICES_WIRED if state["n"] < 3 else
                      "eth0:ethernet:connected:Wired\nwlan0:wifi:connected:curatables-wifi-trial\n")
    fake.on(["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], fn=devices)
    fake.on(["nmcli", "-g", "IP4.ADDRESS"], out="192.0.2.31/24\n")
    flag_seen = []
    fake.on(["nmcli", "--wait"], fn=lambda argv: (flag_seen.append(SETTINGS.wifi_test_flag.exists()),
                                                  Result(0))[1])
    result = wifi.wifi_set({"ssid": "Home Net", "password": "long enough"})(FakeJob())
    assert result["wired"] and flag_seen == [True]
    assert fake.ran("nmcli", "device", "disconnect", "wlan0")


def test_terse_output_with_escaped_colons():
    from syshelper.nm import split_terse
    assert split_terse(r"yes:Cafe\:Guest:70") == ["yes", "Cafe:Guest", "70"]
    assert split_terse(r"a\\b:c") == ["a\\b", "c"]


def test_scan_lists_each_network_once_strongest_first(fake):
    fake.on(["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"], out=DEVICES_WIFI)
    fake.on(["nmcli", "-t", "-e", "yes", "-f", "SSID,SIGNAL,SECURITY"],
            out="Home Net:40:WPA2\nHome Net:80:WPA2\n:90:WPA2\nCafe:60:\n")
    nets = wifi.wifi_scan({})["networks"]
    assert nets == [{"ssid": "Home Net", "signal": 80, "secured": True},
                    {"ssid": "Cafe", "signal": 60, "secured": False}]


# --- power --------------------------------------------------------------------

def test_power_flags_from_vcgencmd(fake):
    fake.on(["vcgencmd", "get_throttled"], out="throttled=0x50005\n")
    fake.on(["vcgencmd", "measure_temp"], out="temp=61.3'C\n")
    p = system.power()
    assert p["under_voltage_now"] and p["under_voltage_seen"] and p["throttled_now"]
    assert not p["soft_temp_limit_seen"]
    assert p["temp_c"] == 61.3


def test_power_is_empty_off_a_pi(fake):
    fake.on(["vcgencmd"], rc=127)
    assert system.power() == {}


# --- drives -------------------------------------------------------------------

def _lsblk(*disks):
    return json.dumps({"blockdevices": list(disks)})


SD = {"name": "mmcblk0", "path": "/dev/mmcblk0", "type": "disk", "size": 32 * 10 ** 9, "tran": None,
      "children": [{"name": "mmcblk0p2", "path": "/dev/mmcblk0p2", "type": "part", "fstype": "ext4",
                    "mountpoint": "/", "size": 31 * 10 ** 9}]}


def _usb(name, fstype, *, uuid="AAAA-1111", label="KIDS", mountpoint=None, model="Thumb"):
    return {"name": name, "path": f"/dev/{name}", "type": "disk", "size": 64 * 10 ** 9, "tran": "usb",
            "vendor": "Acme ", "model": model,
            "children": [{"name": f"{name}1", "path": f"/dev/{name}1", "type": "part", "fstype": fstype,
                          "uuid": uuid, "label": label, "mountpoint": mountpoint,
                          "size": 64 * 10 ** 9, "fsavail": 10 ** 9}]}


def test_only_usb_drives_are_listed(fake):
    fake.on(["lsblk"], out=_lsblk(SD, _usb("sda", "vfat")))
    found = drives.disks()
    assert [d["path"] for d in found] == ["/dev/sda"]
    part = found[0]["partitions"][0]
    assert found[0]["name"] == "Acme Thumb"
    assert part["fs_name"] == "FAT32" and part["usable"] and "4 GB" in part["warning"]


def test_unknown_formats_are_listed_but_not_usable(fake):
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "btrfs"), _usb("sdb", None, uuid="")))
    a, b = (d["partitions"][0] for d in drives.disks())
    assert not a["usable"] and "exFAT" in a["warning"]
    assert not b["usable"] and b["fs_name"] == "not formatted"


def test_the_videos_drive_is_recognised(fake):
    SETTINGS.fstab_path.write_text(
        f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat uid=999,nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "exfat", mountpoint=str(SETTINGS.media_mount))))
    part = drives.disks()[0]["partitions"][0]
    assert part["role"] == "videos" and part["is_videos_drive"]


def test_format_needs_the_word_erase(fake):
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "vfat")))
    with pytest.raises(HelperError, match="ERASE"):
        drives.drive_format({"disk": "/dev/sda", "fstype": "exfat", "label": "KIDS", "confirm": "erase"})


def test_format_refuses_the_sd_card_and_the_videos_drive(fake):
    SETTINGS.fstab_path.write_text(f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk(SD, _usb("sda", "exfat", mountpoint=str(SETTINGS.media_mount))))
    base = {"fstype": "exfat", "label": "KIDS", "confirm": "ERASE"}
    with pytest.raises(HelperError, match="not plugged in"):
        drives.drive_format({**base, "disk": "/dev/mmcblk0"})
    with pytest.raises(HelperError, match="in use"):
        drives.drive_format({**base, "disk": "/dev/sda"})


@pytest.mark.parametrize("device", ["/dev/sda1; reboot", "sda1", "/dev/../etc/passwd"])
def test_device_names_are_checked(fake, device):
    with pytest.raises(HelperError):
        drives.drive_repair({"device": device})


def test_format_partitions_then_makes_an_exfat_filesystem(fake, monkeypatch):
    monkeypatch.setattr(drives.time, "sleep", lambda s: None)
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "ntfs", mountpoint="/media/x")))
    work = drives.drive_format({"disk": "/dev/sda", "fstype": "exfat", "label": "KIDS", "confirm": "ERASE"})
    assert work(FakeJob()) == {"partition": "/dev/sda1"}
    argvs = fake.argvs()
    order = [a[0] for a in argvs if a[0] in ("umount", "wipefs", "sfdisk", "mkfs.exfat")]
    assert order == ["umount", "wipefs", "sfdisk", "mkfs.exfat"]
    sfdisk = next(c for c in fake.calls if c[0][0] == "sfdisk")
    assert drives.BASIC_DATA in sfdisk[1]
    assert ["mkfs.exfat", "-L", "KIDS", "/dev/sda1"] in argvs


def test_repair_of_the_videos_drive_pauses_curatables(fake):
    SETTINGS.fstab_path.write_text(f"UUID=AAAA-1111 {SETTINGS.media_mount} ntfs3 nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "ntfs", mountpoint=str(SETTINGS.media_mount))))
    fake.on(["findmnt"], out="")
    drives.drive_repair({"device": "/dev/sda1"})(FakeJob())
    argvs = fake.argvs()
    stop = argvs.index(["systemctl", "stop", "curatables.service"])
    fix = argvs.index(["ntfsfix", "-d", "/dev/sda1"])
    start = argvs.index(["systemctl", "start", "curatables.service"])
    assert stop < fix < start


def test_failed_repair_says_so(fake):
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "exfat")))
    fake.on(["fsck.exfat"], rc=4)
    with pytest.raises(HelperError, match="could not fix"):
        drives.drive_repair({"device": "/dev/sda1"})(FakeJob())


def test_mount_options_give_fat_style_drives_to_the_service_user(fake):
    fs, opts = drives.mount_options("vfat")
    assert fs == "vfat" and f"uid={os.getuid()}" in opts
    assert drives.mount_options("ntfs")[0] == "ntfs3"
    with pytest.raises(HelperError):
        drives.mount_options("btrfs")


def test_removing_the_videos_drive_from_fstab_keeps_other_lines(fake):
    SETTINGS.fstab_path.write_text(
        "proc /proc proc defaults 0 0\n"
        f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat nofail 0 0\n"
        "UUID=BBBB /home/x ext4 defaults 0 2\n")
    drives._remove_media_fstab(FakeJob())
    assert SETTINGS.fstab_path.read_text() == "proc /proc proc defaults 0 0\nUUID=BBBB /home/x ext4 defaults 0 2\n"
    assert list(SETTINGS.fstab_path.parent.glob("fstab.before-curatables-*"))


def test_eject_videos_drive_restarts_curatables_and_powers_off(fake):
    SETTINGS.fstab_path.write_text(f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "exfat", mountpoint=str(SETTINGS.media_mount))))
    fake.on(["findmnt"], out="")
    assert drives.drive_eject({"device": "/dev/sda1"})(FakeJob()) == {"safe": True}
    argvs = fake.argvs()
    assert argvs.index(["umount", str(SETTINGS.media_mount)]) < argvs.index(
        ["systemctl", "start", "curatables.service"])
    assert ["udisksctl", "power-off", "-b", "/dev/sda"] in argvs


def test_eject_reports_a_busy_drive(fake):
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "exfat", mountpoint="/media/stick")))
    fake.on(["umount"], rc=32, err="target is busy")
    fake.on(["findmnt"], out="/dev/sda1\n")
    with pytest.raises(HelperError, match="still in use"):
        drives.drive_eject({"device": "/dev/sda1"})(FakeJob())


def test_open_for_import_mounts_under_the_drives_folder(fake):
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "exfat", label="Movies 2")))
    result = drives.drive_open_for_import({"device": "/dev/sda1"})(FakeJob())
    assert result["path"] == str(SETTINGS.import_mount_root / "Movies-2")
    mount = next(a for a in fake.argvs() if a[0] == "mount")
    assert mount[-2:] == ["/dev/sda1", result["path"]] and "noexec" in mount[4]


def test_reconnect_needs_the_drive_plugged_in(fake):
    SETTINGS.fstab_path.write_text(f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk())
    with pytest.raises(HelperError, match="not plugged in"):
        drives.drive_reconnect({})


def test_reconnect_mounts_and_restarts(fake):
    SETTINGS.fstab_path.write_text(f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "exfat")))
    fake.on(["findmnt"], out="")
    drives.drive_reconnect({})(FakeJob())
    assert ["mount", str(SETTINGS.media_mount)] in fake.argvs()
    assert ["systemctl", "restart", "curatables.service"] in fake.argvs()


# --- diagnostics --------------------------------------------------------------

def test_redaction_removes_known_secrets_and_hash_shapes():
    text = ("psk=hunter2hunter2\nconnected to Home Net with sekrit-value-1\n"
            "admin:$y$j9T$abc$def:19000\nhash pbkdf2$600000$aa$bb\napi_key: 12345")
    out = diagnostics.redact(text, {"sekrit-value-1"})
    assert "hunter2" not in out and "sekrit-value-1" not in out
    assert "$y$" not in out and "pbkdf2$" not in out and "12345" not in out
    assert "Home Net" in out


def test_secrets_include_the_app_password_hash_and_session_secret(fake):
    (SETTINGS.data_dir / "config.json").write_text(json.dumps({
        "parent": {"password_hash": "pbkdf2:abc:def", "session_secret": "s3ss10n-secret"},
        "imports": {"tmdb_api_key": "tmdb-key-123"}}))
    assert {"pbkdf2:abc:def", "s3ss10n-secret", "tmdb-key-123"} <= diagnostics.secrets()


def test_checks_flag_a_missing_videos_drive(fake):
    SETTINGS.fstab_path.write_text(f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk())
    fake.on(["findmnt"], out="")
    drive = next(c for c in diagnostics.check_storage() if c["id"] == "drive")
    assert drive["status"] == "fail" and "Reconnect" in drive["fix"]


def test_checks_flag_under_voltage(fake):
    fake.on(["vcgencmd", "get_throttled"], out="throttled=0x10000\n")
    power = next(c for c in diagnostics.check_power() if c["id"] == "power")
    assert power["status"] == "warn" and "power supply" in power["fix"]


def test_report_is_redacted(fake, monkeypatch):
    (SETTINGS.data_dir / "config.json").write_text(json.dumps(
        {"parent": {"session_secret": "do-not-leak-this"}}))
    fake.on(["journalctl"], out="something logged do-not-leak-this\n")
    monkeypatch.setattr(diagnostics, "_tcp", lambda host, port, timeout=5: "")
    report = diagnostics.build_report()
    assert "Curatables diagnostics report" in report
    assert "do-not-leak-this" not in report and "[hidden]" in report


def test_report_file_uses_windows_line_endings(tmp_path):
    target = tmp_path / "report.txt"
    diagnostics.write_text_file(str(target), "one\ntwo\n")
    assert target.read_bytes() == b"one\r\ntwo\r\n"


def test_the_app_client_talks_to_the_helper_over_a_socket(tmp_path):
    import threading

    from app.services.system_helper import HelperFailed, HelperUnavailable, SystemHelper

    path = str(tmp_path / "h.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(path)
    listener.listen(4)

    def serve(n):
        for _ in range(n):
            conn, _ = listener.accept()
            server._serve_one(conn, {os.getuid()})

    thread = threading.Thread(target=serve, args=(2,), daemon=True)
    thread.start()
    client = SystemHelper(path, timeout=5)
    assert client.available
    assert client.call("ping") == {"pong": True}
    with pytest.raises(HelperFailed, match="unknown action"):
        client.call("format_everything")
    thread.join(5)
    listener.close()
    os.unlink(path)
    assert not client.available
    with pytest.raises(HelperUnavailable):
        client.call("ping")


# --- files in the service user's folders -------------------------------------

def test_symlinks_in_the_data_dir_are_never_followed(tmp_path):
    from syshelper import safefs
    secret = tmp_path / "shadow"
    secret.write_text("root:$6$secret\n")
    link = tmp_path / "curatables.log"
    link.symlink_to(secret)
    assert safefs.read_owned(link, os.getuid()) is None
    assert safefs.read_owned(secret, os.getuid()) == "root:$6$secret\n"
    assert safefs.read_owned(secret, os.getuid() + 1) is None


def test_owned_writes_replace_a_planted_symlink_instead_of_writing_through_it(tmp_path):
    from syshelper import safefs
    victim = tmp_path / "victim"
    victim.write_text("untouched")
    target = tmp_path / "config.json"
    (tmp_path / "config.json.helper-tmp").symlink_to(victim)
    safefs.write_owned(target, "{}", os.getuid(), os.getgid())
    assert victim.read_text() == "untouched"
    assert target.read_text() == "{}" and not target.is_symlink()
    assert oct(target.stat().st_mode & 0o777) == "0o600"


def test_report_skips_an_app_log_that_points_elsewhere(fake, tmp_path, monkeypatch):
    monkeypatch.setattr(diagnostics, "_tcp", lambda host, port, timeout=5: "")
    secret = tmp_path / "root-only"
    secret.write_text("TOP SECRET LINE\n")
    logs = SETTINGS.data_dir / "logs"
    logs.mkdir()
    (logs / "curatables.log").symlink_to(secret)
    assert "TOP SECRET LINE" not in diagnostics.build_report()
    (logs / "curatables.log").unlink()
    (logs / "curatables.log").write_text("ordinary app line\n")
    assert "ordinary app line" in diagnostics.build_report()


def test_report_carries_the_weeks_warnings_from_every_app_log(fake, monkeypatch):
    monkeypatch.setattr(diagnostics, "_tcp", lambda host, port, timeout=5: "")
    logs = SETTINGS.data_dir / "logs"
    logs.mkdir()
    old = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 10 * 86400))
    recent = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(time.time() - 3 * 86400))
    (logs / "curatables.log.2").write_text(f"{old} [-] ERROR app: too old to matter\n")
    (logs / "curatables.log.1").write_text(
        f"{recent} [abc] ERROR app.jobs: download failed\n"
        "Traceback (most recent call last):\n  ValueError: boom\n"
        f"{recent} [-] INFO app: routine line\n")
    (logs / "curatables.log").write_text(f"{recent} [-] WARNING app: disk getting full\n")
    report = diagnostics.build_report()
    problems = report.split("warnings and errors, last 7 days =====\n", 1)[1].split("=====", 1)[0]
    assert "download failed" in problems and "ValueError: boom" in problems
    assert "disk getting full" in problems
    assert "routine line" not in problems and "too old to matter" not in problems
    assert problems.index("download failed") < problems.index("disk getting full")


def test_report_drops_the_zero_bytes_a_power_cut_leaves_in_the_app_log(fake, monkeypatch):
    monkeypatch.setattr(diagnostics, "_tcp", lambda host, port, timeout=5: "")
    logs = SETTINGS.data_dir / "logs"
    logs.mkdir()
    (logs / "curatables.log").write_bytes(b"before the cut\n" + b"\0" * 86 + b"after the restart\n")
    report = diagnostics.build_report()
    assert "\0" not in report
    assert "before the cut\nafter the restart" in report


def test_config_edits_refuse_a_config_that_is_a_symlink(fake, tmp_path):
    victim = tmp_path / "other.json"
    victim.write_text('{"parent": {"password_hash": "x"}}')
    (SETTINGS.data_dir / "config.json").symlink_to(victim)
    with pytest.raises(HelperError, match="damaged"):
        drives._write_config(lambda cfg: cfg.update(storage={}))
    assert victim.read_text() == '{"parent": {"password_hash": "x"}}'


# --- backups ------------------------------------------------------------------

def test_backups_are_listed_from_both_places(fake, tmp_path, monkeypatch):
    from syshelper import backups
    sd = tmp_path / "backups"
    sd.mkdir()
    monkeypatch.setattr(SETTINGS, "backup_dir", sd)
    (sd / "curatables-2026-10-01-030000.db").write_text("x")
    (sd / "curatables-2026-10-01-030000.config.json").write_text("{}")
    (sd / "notes.txt").write_text("x")
    (sd / "curatables-2026-10-02-030000.db").symlink_to(tmp_path / "elsewhere")
    drive = SETTINGS.media_mount / "curatables-library" / "backups"
    drive.mkdir(parents=True)
    (drive / "curatables-2026-10-03-030000.db").write_text("y")
    found = backups.backups_list({})["backups"]
    assert [(b["where"], b["when"]) for b in found] == [("drive", "2026-10-03 03:00"), ("sd", "2026-10-01 03:00")]


@pytest.mark.parametrize("params", [
    {"where": "sd", "name": "../../etc/shadow"},
    {"where": "usb", "name": "curatables-2026-10-01-030000.db"},
    {"where": "sd", "name": "curatables-2026-10-09-030000.db"},
])
def test_restore_only_accepts_listed_backups(fake, tmp_path, monkeypatch, params):
    from syshelper import backups
    sd = tmp_path / "backups"
    sd.mkdir()
    monkeypatch.setattr(SETTINGS, "backup_dir", sd)
    (sd / "curatables-2026-10-01-030000.db").write_text("x")
    with pytest.raises(HelperError):
        backups.backup_restore(params)


def test_restore_runs_as_the_service_user_with_curatables_stopped(fake, tmp_path, monkeypatch):
    from syshelper import backups
    sd = tmp_path / "backups"
    sd.mkdir()
    monkeypatch.setattr(SETTINGS, "backup_dir", sd)
    (sd / "curatables-2026-10-01-030000.db").write_text("x")
    backups.backup_restore({"where": "sd", "name": "curatables-2026-10-01-030000.db"})(FakeJob())
    argvs = fake.argvs()
    restore = next(i for i, a in enumerate(argvs) if a[:4] == ["runuser", "-u", "curatables", "--"])
    assert argvs.index(["systemctl", "stop", "curatables.service"]) < restore
    assert argvs.index(["systemctl", "start", "curatables.service"]) > restore
    assert argvs[restore][-1] == str(sd / "curatables-2026-10-01-030000.db")


# --- recovery files on the boot partition ------------------------------------

@pytest.fixture
def boot(fake, monkeypatch):
    SETTINGS.boot_dir.mkdir()
    monkeypatch.setattr("syshelper.bootfs.pwd.getpwnam", lambda name: type("P", (), {
        "pw_uid": os.getuid(), "pw_gid": os.getgid()})())
    return SETTINGS.boot_dir


def test_reset_file_clears_the_parent_password_once(boot):
    from syshelper import bootfs
    (SETTINGS.data_dir / "config.json").write_text(json.dumps({
        "parent": {"password_hash": "pbkdf2:x", "session_epoch": 3, "system_setup_done": True},
        "storage": {"cache_days": 9}}))
    (boot / "curatables-reset.txt").write_text("")
    assert bootfs.reset_from_boot() == 0
    cfg = json.loads((SETTINGS.data_dir / "config.json").read_text())
    assert cfg["parent"] == {"password_hash": None, "session_epoch": 4, "system_setup_done": False,
                             "linux_password_reset": True}
    assert cfg["storage"] == {"cache_days": 9}
    assert not (boot / "curatables-reset.txt").exists()
    done = (boot / "curatables-reset-done.txt").read_text()
    assert "password was cleared" in done and "rename this file back" in done
    assert "parent password was cleared" in (boot / "curatables-firstboot.log").read_text()


def test_no_reset_file_changes_nothing(boot):
    from syshelper import bootfs
    (SETTINGS.data_dir / "config.json").write_text('{"parent": {"password_hash": "keep"}}')
    assert bootfs.reset_from_boot() == 0
    assert json.loads((SETTINGS.data_dir / "config.json").read_text())["parent"]["password_hash"] == "keep"


def test_reset_refuses_a_config_that_is_a_symlink(boot, tmp_path):
    from syshelper import bootfs
    victim = tmp_path / "victim.json"
    victim.write_text('{"parent": {"password_hash": "x"}}')
    (SETTINGS.data_dir / "config.json").symlink_to(victim)
    (boot / "curatables-reset.txt").write_text("")
    bootfs.reset_from_boot()
    assert victim.read_text() == '{"parent": {"password_hash": "x"}}'


def test_diagnose_file_writes_a_report_next_to_it(boot, monkeypatch):
    from syshelper import bootfs
    monkeypatch.setattr(diagnostics, "_tcp", lambda host, port, timeout=5: "")
    (boot / "curatables-diagnose.txt").write_text("")
    assert bootfs.report_from_boot() == 0
    report = (boot / "curatables-diagnostics-report.txt").read_bytes()
    assert b"Curatables diagnostics report" in report and b"\r\n" in report
    assert (boot / "curatables-diagnose-done.txt").exists()
    assert not (boot / "curatables-diagnose.txt").exists()


def test_recovery_units_only_run_when_their_file_is_there():
    root = Path(__file__).resolve().parents[1] / "systemd"
    reset = (root / "curatables-recovery.service").read_text()
    assert "ConditionPathExists=/boot/firmware/curatables-reset.txt" in reset
    assert "Before=curatables.service" in reset
    diag = (root / "curatables-diagnose.service").read_text()
    assert "ConditionPathExists=/boot/firmware/curatables-diagnose.txt" in diag


# --- drive and network edge cases --------------------------------------------

def test_format_refuses_the_videos_drive_even_when_it_is_not_mounted(fake):
    SETTINGS.fstab_path.write_text(f"UUID=AAAA-1111 {SETTINGS.media_mount} exfat nofail 0 0\n")
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "exfat")))
    with pytest.raises(HelperError, match="in use"):
        drives.drive_format({"disk": "/dev/sda", "fstype": "exfat", "label": "KIDS", "confirm": "ERASE"})


def test_format_needs_the_mkfs_tool_before_erasing(fake, monkeypatch):
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "vfat")))
    monkeypatch.setattr(drives.shutil, "which", lambda name, path=None: None)
    with pytest.raises(HelperError, match="Nothing was erased"):
        drives.drive_format({"disk": "/dev/sda", "fstype": "exfat", "label": "KIDS", "confirm": "ERASE"})
    assert not fake.ran("wipefs")


def test_a_failed_switch_to_a_new_drive_starts_curatables_again(fake, monkeypatch):
    SETTINGS.fstab_path.write_text(f"UUID=OLD-0000 {SETTINGS.media_mount} exfat nofail 0 0\n")
    (SETTINGS.media_mount / "curatables-library").mkdir(parents=True)
    fake.on(["lsblk"], out=_lsblk(_usb("sdb", "exfat", uuid="NEW-1111", label="BIG")))
    fake.on(["findmnt"], fn=lambda argv: Result(0, "/dev/sda1\n") if str(SETTINGS.media_mount) in argv
            else Result(0, ""))
    fake.on(["umount", str(SETTINGS.media_mount)], rc=32, err="target is busy")
    monkeypatch.setattr(drives, "_copy_library", lambda *a, **k: None)
    with pytest.raises(HelperError, match="still in use"):
        drives.drive_use_for_videos({"device": "/dev/sdb1"})(FakeJob())
    argvs = fake.argvs()
    assert argvs.index(["systemctl", "stop", "curatables.service"]) < argvs.index(
        ["systemctl", "start", "curatables.service"])


def test_ntfsfix_exit_code_one_is_a_failure(fake):
    fake.on(["lsblk"], out=_lsblk(_usb("sda", "ntfs")))
    fake.on(["ntfsfix"], rc=1)
    with pytest.raises(HelperError, match="could not fix"):
        drives.drive_repair({"device": "/dev/sda1"})(FakeJob())


def test_connected_without_internet_still_counts_as_connected(fake):
    fake.on(["nmcli", "-t", "-e", "yes", "-f", "DEVICE,TYPE,STATE,CONNECTION", "device"],
            out="eth0:ethernet:connected (site only):Wired\nwlan0:wifi:disconnected:\n")
    from syshelper import nm
    assert nm.wired_connected()


def test_media_mounts_never_allow_setuid_or_devices(fake):
    for fstype in ("ext4", "exfat", "ntfs", "vfat"):
        opts = drives.mount_options(fstype)[1].split(",")
        assert "nosuid" in opts and "nodev" in opts, fstype


def test_a_failed_nightly_backup_gets_its_own_advice(fake):
    fake.on(["systemctl", "is-active"], out="active")
    fake.on(["systemctl", "--failed"], out="curatables-backup.service loaded failed failed Curatables backup\n")
    checks = {c["id"]: c for c in diagnostics.check_services()}
    assert checks["backup"]["status"] == "warn" and "Back up now" in checks["backup"]["fix"]
    assert "units" not in checks


# --- clock ----------------------------------------------------------------------

def test_the_clock_is_stepped_forward_while_ntp_has_not_synced(fake, monkeypatch, tmp_path):
    saved = tmp_path / "timesync-clock"
    saved.write_text("")
    os.utime(saved, (1, 1))
    monkeypatch.setattr(system, "TIMESYNC_CLOCK_FILE", saved)
    fake.on(["timedatectl"], out="no\n")
    ahead = int(time.time()) + 3 * 86400
    assert system.set_clock({"epoch": ahead})["set"] is True
    assert fake.ran("date", "-u", "-s", f"@{ahead}")
    # Saved for the next boot (the real clock moved, so the file's
    # mtime is "now" again rather than the stale time it had).
    assert saved.stat().st_mtime > 1


def test_the_clock_is_left_alone_once_ntp_synced(fake):
    fake.on(["timedatectl"], out="yes\n")
    assert system.set_clock({"epoch": int(time.time()) + 86400}) == {
        "set": False, "reason": "synchronized"}
    assert not fake.ran("date")


def test_the_clock_never_goes_back_or_years_ahead(fake):
    fake.on(["timedatectl"], out="no\n")
    assert system.set_clock({"epoch": int(time.time()) - 86400})["set"] is False
    with pytest.raises(HelperError):
        system.set_clock({"epoch": int(time.time()) + 5 * 365 * 86400})
    with pytest.raises(HelperError):
        system.set_clock({"epoch": "tomorrow"})
    assert not fake.ran("date")
