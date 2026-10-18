"""scripts/setup-media-drive.sh in a sandbox: a copy that fails or is
cancelled leaves the box as it was, and a copy cut short is copied again."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "setup-media-drive.sh"
UUID = "ABCD-1234"


def _shim(bin_dir: Path, name: str, body: str) -> None:
    path = bin_dir / name
    path.write_text("#!/bin/bash\n" + body + "\n")
    path.chmod(0o755)


@pytest.fixture
def box(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    state = tmp_path / "state"
    state.mkdir()
    mnt = tmp_path / "media"
    real_cp = subprocess.run(["bash", "-c", "command -v cp"], capture_output=True,
                             text=True).stdout.strip()
    # "Mounting" just records it; the mount point stays a plain folder.
    _shim(bin_dir, "mountpoint", f'[ -e "{state}/mounted" ]')
    _shim(bin_dir, "mount", f'touch "{state}/mounted"')
    _shim(bin_dir, "umount", f'rm -f "{state}/mounted"')
    _shim(bin_dir, "findmnt", f'''[ -e "{state}/mounted" ] || exit 0
case " $* " in *" --source "*) echo "{mnt}" ;; *) echo /dev/sdz1 ;; esac''')
    for name in ("systemctl", "chattr", "chown"):
        _shim(bin_dir, name, "exit 0")
    _shim(bin_dir, "runuser", 'shift 3; exec "$@"')
    # cp of a media folder: CP_MODE=fail copies a little then fails,
    # CP_MODE=hang copies a little then waits to be stopped.
    _shim(bin_dir, "cp", f'''
for a; do last=$a; done
case "$last" in */curatables-library*)
  case "${{CP_MODE:-}}" in
    fail) mkdir -p "$last/videos"; echo x > "$last/videos/half"; exit 1 ;;
    hang) mkdir -p "$last/videos"; echo x > "$last/videos/half"; sleep 30 ;;
  esac ;;
esac
exec {real_cp} "$@"''')
    data = tmp_path / "data"
    (data / "videos" / "v1").mkdir(parents=True)
    (data / "videos" / "v1" / "video.mp4").write_bytes(b"x" * 1000)
    (data / "thumbnails").mkdir()
    (data / "thumbnails" / "v1.jpg").write_bytes(b"j" * 10)
    (data / "config.json").write_text(json.dumps({"storage": {"path": str(data)}}))
    fstab = tmp_path / "fstab"
    fstab.write_text("proc /proc proc defaults 0 0\n")
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}",
               CURATABLES_MEDIA_MOUNT=str(mnt), CURATABLES_FSTAB=str(fstab))
    driver = (
        "set -euo pipefail\n"
        'die() { echo "ERROR: $*" >&2; exit 1; }\n'
        'say() { echo "==> $*"; }\n'
        f'DEV=/dev/sdz1 FSTYPE=exfat UUID={UUID} DATA="{data}" SVC_USER="$(id -un)" '
        f'DROPIN_DIR="{tmp_path / "dropin"}"\n'
        f'. "{SCRIPT}"\n')

    class Box:
        pass
    b = Box()
    b.tmp, b.data, b.mnt, b.lib, b.fstab, b.state = tmp_path, data, mnt, mnt / "curatables-library", fstab, state

    def start(cp_mode=""):
        return subprocess.Popen(["bash", "-c", driver], env=dict(env, CP_MODE=cp_mode),
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                start_new_session=True)
    b.start = start
    return b


def _as_before(box):
    assert not box.lib.exists() and not (box.mnt / "curatables-library.incomplete").exists()
    assert UUID not in box.fstab.read_text()
    assert not (box.state / "mounted").exists()
    assert (box.data / "videos" / "v1" / "video.mp4").exists()
    assert "media_path" not in json.loads((box.data / "config.json").read_text())["storage"]


def test_a_failed_copy_is_undone(box):
    proc = box.start("fail")
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode != 0, out
    assert "Removing the partial copy" in out and "still on the SD card" in out
    _as_before(box)


def test_a_copy_stopped_from_the_drives_page_is_undone(box):
    proc = box.start("hang")
    deadline = time.monotonic() + 30
    while not (box.lib / "videos" / "half").exists():
        assert time.monotonic() < deadline
        time.sleep(0.05)
    os.killpg(proc.pid, signal.SIGTERM)
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode != 0
    assert "Removing the partial copy" in out
    _as_before(box)


def test_a_copy_cut_short_earlier_is_copied_again(box):
    (box.state / "mounted").touch()   # the drive is in, with last time's half copy
    (box.lib / "videos").mkdir(parents=True)
    (box.lib / "videos" / "half").write_text("x")
    (box.mnt / "curatables-library.incomplete").write_text("")
    proc = box.start()
    out, _ = proc.communicate(timeout=60)
    assert proc.returncode == 0, out
    assert "never finished" in out and "already holds" not in out
    assert (box.lib / "videos" / "v1" / "video.mp4").exists()
    assert not (box.lib / "videos" / "half").exists()
    assert not (box.mnt / "curatables-library.incomplete").exists()
    assert UUID in box.fstab.read_text()
    assert json.loads((box.data / "config.json").read_text())["storage"]["media_path"] == str(box.lib)
