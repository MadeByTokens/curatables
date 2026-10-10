"""USB drives: list them, use one for videos, move to another, eject,
reconnect, repair, format, and open one for importing.

Only USB drives are ever touched. The SD card (and anything holding the
running system) is refused by every action that changes something.
"""

from __future__ import annotations

import json
import os
import pwd
import re
import shutil
import time
from pathlib import Path

from syshelper import runner, safefs, system
from syshelper.errors import HelperError
from syshelper.jobs import Job
from syshelper.registry import action, text
from syshelper.settings import SETTINGS

LIBRARY = "curatables-library"
MEDIA_SUBDIRS = ("videos", "uploads", "thumbnails", "channels")
FAT_LIMIT = 4 * 1024 ** 3 - 1
FS_NAMES = {"exfat": "exFAT", "ntfs": "NTFS", "vfat": "FAT32", "ext4": "ext4"}
USABLE = {"exfat", "ntfs", "vfat", "ext4"}
BASIC_DATA = "EBD0A0A2-B9E5-4433-87C0-68B6B72699C7"
LINUX_DATA = "0FC63DAF-8483-4772-8E7D-3D69D8477DE4"
LSBLK_COLUMNS = "NAME,PATH,TYPE,SIZE,FSTYPE,LABEL,UUID,MOUNTPOINT,FSAVAIL,FSSIZE,TRAN,RM,HOTPLUG,MODEL,VENDOR,PKNAME"


# --- reading the state ------------------------------------------------------

def _lsblk() -> list[dict]:
    result = runner.run(["lsblk", "-J", "-b", "-o", LSBLK_COLUMNS])
    if not result.ok:
        raise HelperError("Could not list the drives: " + result.text)
    return list(json.loads(result.out or "{}").get("blockdevices", []))


def _num(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _media_fstab() -> dict:
    """The fstab line that mounts the videos drive, if there is one."""
    try:
        lines = SETTINGS.fstab_path.read_text().splitlines()
    except OSError:
        return {}
    for line in lines:
        fields = line.split()
        if len(fields) >= 4 and not line.lstrip().startswith("#") and fields[1] == str(SETTINGS.media_mount):
            uuid = fields[0][5:] if fields[0].startswith("UUID=") else ""
            return {"uuid": uuid, "fstype": fields[2], "line": line}
    return {}


def _mounted_at(path: Path) -> str:
    result = runner.run(["findmnt", "-n", "-o", "SOURCE", "--mountpoint", str(path)])
    return result.out.strip() if result.ok else ""


def _is_usb(disk: dict) -> bool:
    name = disk.get("name") or ""
    if name.startswith(("mmcblk", "loop", "zram", "ram", "nvme")) and disk.get("tran") != "usb":
        return False
    return disk.get("tran") == "usb"


def _role(part: dict) -> str:
    mnt = part.get("mountpoint") or ""
    if mnt == str(SETTINGS.media_mount):
        return "videos"
    if mnt and mnt.startswith(str(SETTINGS.import_mount_root) + "/"):
        return "import"
    if mnt and (mnt == str(SETTINGS.data_dir) or mnt.startswith(str(SETTINGS.data_dir) + "/")):
        return "library"
    if mnt in ("/", "/boot", "/boot/firmware") or mnt.startswith("/usr"):
        return "system"
    return "other" if mnt else ""


def _partition_info(part: dict, media_uuid: str) -> dict:
    fstype = part.get("fstype") or ""
    info = {
        "path": part.get("path"), "label": part.get("label") or "", "uuid": part.get("uuid") or "",
        "fstype": fstype, "fs_name": FS_NAMES.get(fstype, fstype or "not formatted"),
        "size": _num(part.get("size")), "free": _num(part.get("fsavail")),
        "mountpoint": part.get("mountpoint") or "", "role": _role(part),
        "usable": fstype in USABLE, "is_videos_drive": bool(media_uuid) and part.get("uuid") == media_uuid,
        "warning": "",
    }
    if fstype == "vfat":
        info["warning"] = "FAT32 cannot hold files over 4 GB, so long movies at high quality will not fit."
    elif fstype and fstype not in USABLE:
        info["warning"] = f"The box cannot use {fstype} drives. Format it as exFAT to use it."
    elif not fstype:
        info["warning"] = "This drive has no format the box can read. Format it to use it."
    return info


def disks() -> list[dict]:
    media_uuid = _media_fstab().get("uuid", "")
    out = []
    for disk in _lsblk():
        if disk.get("type") != "disk" or not _is_usb(disk):
            continue
        parts = [p for p in disk.get("children") or [] if p.get("type") == "part"]
        if not parts and disk.get("fstype"):
            parts = [disk]
        name = " ".join(x for x in ((disk.get("vendor") or "").strip(), (disk.get("model") or "").strip()) if x)
        out.append({
            "path": disk.get("path"), "name": name or "USB drive", "size": _num(disk.get("size")),
            "partitions": [_partition_info(p, media_uuid) for p in parts],
        })
    return out


def _find_partition(path: str) -> tuple[dict, dict]:
    for disk in disks():
        for part in disk["partitions"]:
            if part["path"] == path:
                return disk, part
    raise HelperError("That drive is not plugged in (or is not a USB drive).")


def _find_disk(path: str) -> dict:
    for disk in disks():
        if disk["path"] == path:
            return disk
    raise HelperError("That drive is not plugged in (or is not a USB drive).")


def media_state() -> dict:
    entry = _media_fstab()
    state = {"configured": bool(entry), "uuid": entry.get("uuid", ""),
             "fstype": entry.get("fstype", ""), "mounted": False, "present": False,
             "device": "", "free": 0, "size": 0, "sd_copy_bytes": _sd_copy_bytes()}
    if not entry:
        return state
    state["mounted"] = bool(_mounted_at(SETTINGS.media_mount))
    for disk in disks():
        for part in disk["partitions"]:
            if part["uuid"] == entry.get("uuid"):
                state.update(present=True, device=part["path"], free=part["free"], size=part["size"],
                             drive_name=disk["name"])
    if state["mounted"]:
        try:
            usage = shutil.disk_usage(SETTINGS.media_mount)
            state["free"], state["size"] = usage.free, usage.total
        except OSError:
            pass
    return state


def _sd_copy_bytes() -> int:
    total = 0
    for keep in SETTINGS.data_dir.glob("media.before-external-*"):
        for root, _dirs, files in os.walk(keep):
            for name in files:
                try:
                    total += os.lstat(os.path.join(root, name)).st_size
                except OSError:
                    pass
    return total


@action("drives_list")
def drives_list(params: dict) -> dict:
    return {"disks": disks(), "media": media_state(), "power": system.power(),
            "import_mounts": _import_mounts()}


def _import_mounts() -> list[str]:
    root = SETTINGS.import_mount_root
    try:
        return sorted(str(p) for p in root.iterdir() if p.is_dir() and _mounted_at(p))
    except OSError:
        return []


# --- small steps --------------------------------------------------------------

def _svc_ids() -> tuple[int, int]:
    entry = pwd.getpwnam(SETTINGS.service_user)
    return entry.pw_uid, entry.pw_gid


def mount_options(fstype: str, *, read_only: bool = False) -> tuple[str, str]:
    """(filesystem type for mount, options) for a drive the service user
    must be able to write."""
    uid, gid = _svc_ids()
    owned = f"uid={uid},gid={gid},fmask=0137,dmask=0027,nosuid,nodev"
    if fstype == "ext4":
        fs, opts = "ext4", "defaults,noatime,nosuid,nodev"
    elif fstype == "exfat":
        fs, opts = "exfat", owned + ",noatime"
    elif fstype == "ntfs":
        fs, opts = "ntfs3", owned + ",noatime"
    elif fstype == "vfat":
        fs, opts = "vfat", owned + ",noatime,utf8"
    else:
        raise HelperError(f"The box cannot use {FS_NAMES.get(fstype, fstype)} drives.")
    if read_only:
        opts += ",ro"
    return fs, opts


def _stop_curatables(job: Job) -> None:
    job.log("Pausing Curatables")
    runner.run(["systemctl", "stop", "curatables.service"], timeout=120)


def _start_curatables(job: Job) -> None:
    job.log("Starting Curatables again")
    runner.run(["systemctl", "daemon-reload"], timeout=60)
    runner.run(["systemctl", "start", "curatables.service"], timeout=120)


def _umount(path: str, job: Job | None = None) -> None:
    runner.run(["sync"], timeout=600)
    result = runner.run(["umount", path], timeout=120)
    if not result.ok and _mounted_at(Path(path)):
        busy = runner.run(["fuser", "-vm", path])
        raise HelperError("The drive is still in use and could not be safely removed. "
                          "Try again in a minute. " + busy.text[:300])


def _remove_media_fstab(job: Job) -> None:
    lines = SETTINGS.fstab_path.read_text().splitlines(keepends=True)
    keep = [line for line in lines
            if not (len(line.split()) >= 2 and not line.lstrip().startswith("#")
                    and line.split()[1] == str(SETTINGS.media_mount))]
    if keep != lines:
        backup = SETTINGS.fstab_path.with_name(f"fstab.before-curatables-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(SETTINGS.fstab_path, backup)
        tmp = SETTINGS.fstab_path.with_name("fstab.curatables-tmp")
        tmp.write_text("".join(keep))
        os.chmod(tmp, 0o644)
        os.replace(tmp, SETTINGS.fstab_path)
        job.log("Removed the old drive from the list of drives mounted at start-up")


def _run_setup_script(job: Job, device: str) -> None:
    script = SETTINGS.code_dir / "scripts" / "setup-external-drive.sh"
    lines: list[str] = []

    def on_line(line: str) -> None:
        lines.append(line)
        job.log(line[4:] if line.startswith("==> ") else line)

    rc = runner.stream([str(script), "--media-only", device], on_line,
                       env={"CURATABLES_DATA": str(SETTINGS.data_dir),
                            "CURATABLES_USER": SETTINGS.service_user,
                            "CURATABLES_MEDIA_MOUNT": str(SETTINGS.media_mount)})
    if rc != 0:
        errors = [line for line in lines if line.startswith("ERROR")]
        runner.run(["systemctl", "start", "curatables.service"], timeout=120)
        raise HelperError((errors[-1][7:] if errors else "Setting up the drive failed.")
                          + " Curatables was started again with its old settings.")


def _tree_stats(root: Path) -> tuple[int, int]:
    files = size = 0
    for base, _dirs, names in os.walk(root):
        for name in names:
            if name.startswith("."):
                continue
            files += 1
            try:
                size += os.lstat(os.path.join(base, name)).st_size
            except OSError:
                pass
    return files, size


def _as_service_user(argv: list[str]) -> list[str]:
    return ["runuser", "-u", SETTINGS.service_user, "--", *argv]


def _copy_library(job: Job, src: Path, dst: Path, same_owners: bool, *, as_service_user: bool = False) -> None:
    files, size = _tree_stats(src)
    free = shutil.disk_usage(dst.parent).free
    if free < size + 512 * 1024 ** 2:
        raise HelperError(f"The new drive has {_gb(free)} free but the videos need {_gb(size)}.")
    job.log(f"Copying {files} files ({_gb(size)}). This can take a long time; leave the box on.")
    copy = ["cp", "-R", "--preserve=timestamps", f"{src}/.", str(dst)]
    if as_service_user:
        runner.run(_as_service_user(["mkdir", "-p", str(dst)]))
        copy = _as_service_user(copy)
    else:
        dst.mkdir(parents=True, exist_ok=True)
    rc = runner.stream(copy, job.log)
    runner.run(["sync"], timeout=3600)
    copied_files, copied_size = _tree_stats(dst)
    if rc != 0 or copied_files < files or (same_owners and copied_size < size):
        raise HelperError(f"The copy is incomplete ({copied_files} of {files} files). "
                          "Nothing was switched; the videos are still on the old drive.")


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.1f} GB"


def _device_arg(params: dict, key: str = "device") -> str:
    value = text(params, key, max_len=64)
    if not re.fullmatch(r"/dev/[A-Za-z0-9]+", value):
        raise HelperError("That is not a drive.")
    return value


def _write_config(update) -> None:
    path = SETTINGS.data_dir / "config.json"
    uid, gid = _svc_ids()
    data = safefs.read_owned_json(path, uid)
    if data is None:
        if os.path.lexists(path):
            raise HelperError("The settings file looks damaged, so it was not changed.")
        data = {}
    update(data)
    safefs.write_owned(path, json.dumps(data, indent=2), uid, gid)


# --- actions ------------------------------------------------------------------

@action("drive_use_for_videos", job=True, title="Setting up the videos drive")
def drive_use_for_videos(params: dict):
    device = _device_arg(params)
    _disk, part = _find_partition(device)
    if part["role"] in ("system", "library"):
        raise HelperError("That drive holds the system and cannot be used for videos.")
    if not part["usable"]:
        raise HelperError(part["warning"] or "The box cannot use that drive.")
    if part["is_videos_drive"] and part["role"] == "videos":
        raise HelperError("That drive already holds the videos.")

    def work(job: Job) -> dict:
        if part["role"] in ("import", "other"):
            job.log("Closing the drive's import folder first")
            _umount(part["mountpoint"], job)
        state = media_state()
        if state["configured"] and state["mounted"] and state["uuid"] != part["uuid"]:
            _move_to(job, device, part)
        else:
            if state["configured"] and state["uuid"] != part["uuid"]:
                _stop_curatables(job)
                try:
                    _remove_media_fstab(job)
                except Exception:
                    _start_curatables(job)
                    raise
            _run_setup_script(job, device)
        return {"device": device, "fat32": part["fstype"] == "vfat"}

    return work


def _move_to(job: Job, device: str, part: dict) -> None:
    trial = SETTINGS.socket_path.parent / "new-drive"
    trial.mkdir(parents=True, exist_ok=True)
    fs, opts = mount_options(part["fstype"])
    result = runner.run(["mount", "-t", fs, "-o", opts, device, str(trial)], timeout=120)
    if not result.ok:
        raise HelperError("The new drive could not be opened: " + result.text
                          + " Try Repair on it first.")
    try:
        target = trial / LIBRARY
        if target.exists() and any(target.iterdir()):
            job.log("The new drive already has a curatables-library; it will be used as it is")
        else:
            _copy_library(job, SETTINGS.media_mount / LIBRARY, target, part["fstype"] == "ext4")
    finally:
        _umount(str(trial), job)
    _stop_curatables(job)
    try:
        _umount(str(SETTINGS.media_mount), job)
        _remove_media_fstab(job)
    except Exception:
        _back_to_old_drive(job)
        raise
    job.log("The old drive keeps its copy of the videos; you can unplug it once this finishes")
    _run_setup_script(job, device)


def _back_to_old_drive(job: Job) -> None:
    """A switch failed before the new drive was set up: reopen the old
    videos drive if it is still listed, and start Curatables again."""
    if _media_fstab() and not _mounted_at(SETTINGS.media_mount):
        runner.run(["mount", str(SETTINGS.media_mount)], timeout=120)
    _start_curatables(job)


@action("drive_back_to_sd", job=True, title="Moving videos back to the SD card")
def drive_back_to_sd(params: dict):
    state = media_state()
    if not state["configured"]:
        raise HelperError("The videos are already on the SD card.")
    if not state["mounted"]:
        raise HelperError("The videos drive is not connected. Plug it in and press Reconnect first.")

    def work(job: Job) -> dict:
        lib = SETTINGS.media_mount / LIBRARY
        _files, size = _tree_stats(lib)
        free = shutil.disk_usage(SETTINGS.data_dir).free
        if free < size + 2 * 1024 ** 3:
            raise HelperError(f"The SD card has {_gb(free)} free but the videos need {_gb(size)} "
                              "(plus 2 GB to spare).")
        _stop_curatables(job)
        try:
            staging = SETTINGS.data_dir / ".from-drive"
            if staging.exists():
                shutil.rmtree(staging)
            _copy_library(job, lib, staging, True, as_service_user=True)
            for sub in MEDIA_SUBDIRS:
                src, dst = staging / sub, SETTINGS.data_dir / sub
                if src.exists():
                    if dst.exists():
                        shutil.rmtree(dst)
                    os.replace(src, dst)
            shutil.rmtree(staging, ignore_errors=True)

            def update(cfg: dict) -> None:
                storage = cfg.setdefault("storage", {})
                storage["media_path"] = ""
                storage["import_roots"] = [r for r in storage.get("import_roots") or []
                                           if r != str(SETTINGS.media_mount)]
            _write_config(update)
            _umount(str(SETTINGS.media_mount), job)
            _remove_media_fstab(job)
            (SETTINGS.dropin_dir / "media-drive.conf").unlink(missing_ok=True)
        finally:
            _start_curatables(job)
        job.log("The videos are on the SD card again. The drive keeps its copy; you can unplug it.")
        return {}

    return work


@action("drive_eject", job=True, title="Ejecting a drive")
def drive_eject(params: dict):
    device = _device_arg(params)
    disk, part = _find_partition(device)
    if part["role"] in ("system", "library"):
        raise HelperError("That drive holds the system and cannot be ejected here.")

    def work(job: Job) -> dict:
        if part["role"] == "videos":
            _stop_curatables(job)
            try:
                _umount(str(SETTINGS.media_mount), job)
            finally:
                _start_curatables(job)
        elif part["mountpoint"]:
            _umount(part["mountpoint"], job)
            _forget_import_mount(part["mountpoint"])
        for other in disk["partitions"]:
            if other["path"] != device and other["mountpoint"] and other["role"] in ("import", "other"):
                _umount(other["mountpoint"], job)
        power = runner.run(["udisksctl", "power-off", "-b", disk["path"]], timeout=60)
        if not power.ok:
            job.log("The drive could not be switched off, but it is safe to unplug")
        job.log("Safe to unplug")
        return {"safe": True}

    return work


@action("drive_reconnect", job=True, title="Reconnecting the videos drive")
def drive_reconnect(params: dict):
    state = media_state()
    if not state["configured"]:
        raise HelperError("No videos drive is set up.")
    if not state["present"]:
        raise HelperError("The videos drive is not plugged in. Plug it in, wait a few seconds, "
                          "and press Reconnect again.")

    def work(job: Job) -> dict:
        if not media_state()["mounted"]:
            job.log("Opening the drive")
            result = runner.run(["mount", str(SETTINGS.media_mount)], timeout=120)
            if not result.ok:
                raise HelperError("The drive could not be opened. It may need a repair "
                                  "(press Repair). Details: " + result.text)
        runner.run(["systemctl", "restart", "curatables.service"], timeout=120)
        job.log("The videos drive is connected")
        return {"mounted": True}

    return work


FSCK_OK = {"exfat": (0, 1), "vfat": (0, 1), "ext4": (0, 1), "ntfs": (0,)}
FSCK = {
    "exfat": ["fsck.exfat", "-y"],
    "vfat": ["fsck.vfat", "-a", "-w"],
    "ntfs": ["ntfsfix", "-d"],
    "ext4": ["e2fsck", "-f", "-y"],
}


@action("drive_repair", job=True, title="Checking and repairing a drive")
def drive_repair(params: dict):
    device = _device_arg(params)
    _disk, part = _find_partition(device)
    if part["role"] in ("system", "library"):
        raise HelperError("That drive holds the system and cannot be repaired here.")
    if part["fstype"] not in FSCK:
        raise HelperError("The box cannot check this kind of drive.")

    def work(job: Job) -> dict:
        videos = part["is_videos_drive"]
        if part["role"] == "videos":
            _stop_curatables(job)
        try:
            if part["mountpoint"]:
                _umount(part["mountpoint"], job)
            job.log(f"Checking the {part['fs_name']} drive (this can take a while)")
            rc = runner.stream([*FSCK[part["fstype"]], device], job.log, timeout=6 * 3600)
            if rc not in FSCK_OK[part["fstype"]]:
                raise HelperError("The check found problems it could not fix. Copy what you need "
                                  "to a computer and format the drive.")
            job.log("The drive is fine" if rc == 0 else "Problems were found and fixed")
            if videos:
                runner.run(["mount", str(SETTINGS.media_mount)], timeout=120)
        finally:
            if videos:
                _start_curatables(job)
        return {"repaired": rc == 1}

    return work


@action("drive_format", job=True, title="Formatting a drive")
def drive_format(params: dict):
    disk_path = _device_arg(params, "disk")
    fstype = text(params, "fstype", max_len=8)
    label = text(params, "label", max_len=11)
    confirm = text(params, "confirm", max_len=16)
    if fstype not in ("exfat", "ext4"):
        raise HelperError("Choose exFAT or ext4.")
    if not re.fullmatch(r"[A-Za-z0-9_-][A-Za-z0-9 _-]{0,10}", label):
        raise HelperError("The name is 1 to 11 letters, digits, spaces, '-' or '_'.")
    if confirm != "ERASE":
        raise HelperError("Type ERASE to confirm that everything on the drive will be deleted.")
    disk = _find_disk(disk_path)
    for part in disk["partitions"]:
        if part["role"] in ("system", "library", "videos") or part["is_videos_drive"]:
            raise HelperError("That drive is in use by the box (it holds the videos or the system). "
                              "Move the videos off it first.")

    mkfs = "mkfs.exfat" if fstype == "exfat" else "mkfs.ext4"
    if not shutil.which(mkfs, path=runner.ENV["PATH"]):
        raise HelperError(f"The box cannot make {FS_NAMES[fstype]} drives ({mkfs} is missing). Nothing was erased.")

    def work(job: Job) -> dict:
        for part in disk["partitions"]:
            if part["mountpoint"]:
                _umount(part["mountpoint"], job)
                _forget_import_mount(part["mountpoint"])
        job.log("Erasing the old layout")
        runner.run(["wipefs", "-a", disk_path], timeout=120)
        layout = f"label: gpt\ntype={LINUX_DATA if fstype == 'ext4' else BASIC_DATA}\n"
        result = runner.run(["sfdisk", "--wipe", "always", disk_path], input=layout, timeout=120)
        if not result.ok:
            raise HelperError("The drive could not be partitioned: " + result.text)
        runner.run(["partprobe", disk_path], timeout=60)
        runner.run(["udevadm", "settle"], timeout=60)
        part_path = _first_partition(disk_path)
        job.log(f"Formatting as {FS_NAMES[fstype]}")
        if fstype == "exfat":
            argv = ["mkfs.exfat", "-L", label, part_path]
        else:
            argv = ["mkfs.ext4", "-F", "-L", label, part_path]
        rc = runner.stream(argv, job.log, timeout=3600)
        if rc != 0:
            raise HelperError("Formatting failed. The drive may be faulty.")
        runner.run(["udevadm", "settle"], timeout=60)
        job.log("The drive is ready")
        return {"partition": part_path}

    return work


def _first_partition(disk_path: str) -> str:
    for _ in range(10):
        for disk in _lsblk():
            if disk.get("path") == disk_path:
                for child in disk.get("children") or []:
                    if child.get("type") == "part":
                        return str(child["path"])
        time.sleep(1)
    raise HelperError("The new partition did not appear. Unplug the drive, plug it in again and retry.")


def _import_dir_for(part: dict) -> Path:
    name = re.sub(r"[^A-Za-z0-9_-]+", "-", part["label"]).strip("-") or part["uuid"] or "drive"
    return SETTINGS.import_mount_root / name[:40]


def _forget_import_mount(mountpoint: str) -> None:
    path = Path(mountpoint)
    if path.parent == SETTINGS.import_mount_root:
        try:
            path.rmdir()
        except OSError:
            pass


@action("drive_open_for_import", job=True, title="Opening a drive for importing")
def drive_open_for_import(params: dict):
    device = _device_arg(params)
    _disk, part = _find_partition(device)
    if part["role"] in ("system", "library"):
        raise HelperError("That drive holds the system.")
    if part["role"] == "videos":
        raise HelperError("This is the videos drive; its other folders can already be imported.")
    if not part["usable"]:
        raise HelperError(part["warning"] or "The box cannot read that drive.")

    def work(job: Job) -> dict:
        if part["role"] == "import":
            return {"path": part["mountpoint"]}
        if part["mountpoint"]:
            _umount(part["mountpoint"], job)
        target = _import_dir_for(part)
        target.mkdir(parents=True, exist_ok=True)
        fs, opts = mount_options(part["fstype"])
        result = runner.run(["mount", "-t", fs, "-o", opts + ",noexec", device, str(target)],
                            timeout=120)
        if not result.ok:
            _forget_import_mount(str(target))
            raise HelperError("The drive could not be opened. It may need a repair (press Repair). "
                              "Details: " + result.text)
        job.log(f"The drive's folders are at {target}")
        return {"path": str(target)}

    return work


@action("drive_delete_sd_copy", job=True, title="Deleting the old copy on the SD card")
def drive_delete_sd_copy(params: dict):
    state = media_state()
    if not (state["configured"] and state["mounted"]):
        raise HelperError("Keep the SD-card copy until the videos drive is connected and working.")
    copies = sorted(SETTINGS.data_dir.glob("media.before-external-*"))
    if not copies:
        raise HelperError("There is no old copy on the SD card.")

    def work(job: Job) -> dict:
        for path in copies:
            if path.is_dir() and not path.is_symlink():
                job.log(f"Deleting {path.name}")
                shutil.rmtree(path)
        return {"deleted": len(copies)}

    return work
