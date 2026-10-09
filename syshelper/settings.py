"""Where things are. Read from the environment so tests and other
installs can point the helper elsewhere."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    socket_path: Path
    service_user: str
    data_dir: Path
    code_dir: Path
    media_mount: Path
    import_mount_root: Path
    boot_dir: Path
    shadow_path: Path
    passwd_path: Path
    fstab_path: Path
    dropin_dir: Path
    wifi_test_flag: Path
    backup_dir: Path
    admin_user: str


def from_env() -> Settings:
    env = os.environ.get
    return Settings(
        socket_path=Path(env("CURATABLES_HELPER_SOCKET", "/run/curatables-helper/helper.sock")),
        service_user=env("CURATABLES_USER", "curatables"),
        data_dir=Path(env("CURATABLES_DATA", "/home/curatables/curatables-data")),
        code_dir=Path(env("CURATABLES_ROOT", "/opt/curatables")),
        media_mount=Path(env("CURATABLES_MEDIA_MOUNT", "/mnt/curatables-media")),
        import_mount_root=Path(env("CURATABLES_IMPORT_MOUNTS", "/mnt/curatables-drives")),
        boot_dir=Path(env("CURATABLES_BOOT_DIR", "/boot/firmware")),
        shadow_path=Path(env("CURATABLES_SHADOW", "/etc/shadow")),
        passwd_path=Path(env("CURATABLES_PASSWD", "/etc/passwd")),
        fstab_path=Path(env("CURATABLES_FSTAB", "/etc/fstab")),
        dropin_dir=Path(env("CURATABLES_DROPIN_DIR", "/etc/systemd/system/curatables.service.d")),
        wifi_test_flag=Path(env("CURATABLES_WIFI_TEST_FLAG", "/run/curatables-wifi-test")),
        backup_dir=Path(env("CURATABLES_BACKUP_DIR", "/var/lib/curatables-backups")),
        admin_user=env("CURATABLES_ADMIN_USER", ""),
    )


SETTINGS = from_env()
