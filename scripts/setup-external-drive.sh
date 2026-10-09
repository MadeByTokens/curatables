#!/usr/bin/env bash
# Put the curatables library on an external drive (USB HDD/SSD).
#
# Usage:
#   sudo /opt/curatables/scripts/setup-external-drive.sh /dev/sda1
#   sudo /opt/curatables/scripts/setup-external-drive.sh --media-only /dev/sda1
#
# Two modes:
#
# WHOLE LIBRARY (default): the drive is mounted AT the data dir, so the
# database moves too. The partition must already be ext4 (FAT/exFAT/NTFS
# cannot hold Linux file ownership and are unreliable for the SQLite
# database). To format a NEW, EMPTY drive (this erases everything on it):
#   sudo mkfs.ext4 -L curatables /dev/sda1
#
#   1. stops curatables
#   2. copies the current data dir onto the drive (skipped if the drive
#      already holds a curatables library)
#   3. keeps the old SD-card copy as <data dir>.before-external-<date>
#   4. adds an /etc/fstab entry (by UUID, with nofail so the Pi still
#      boots without the drive) that mounts the drive AT the data dir
#   5. installs a systemd drop-in that sets CURATABLES_REQUIRE_MOUNT=1,
#      so curatables refuses to run if the drive is missing instead of
#      silently starting an empty library on the SD card
#   6. starts curatables again
#
# MEDIA ONLY (--media-only): the database, settings and logs stay on the
# SD card; videos, uploads, thumbnails and channel pictures go to a
# curatables-library/ folder on the drive. The drive may be ext4, exFAT
# or NTFS and may already hold other files (e.g. a movie collection to
# import), which are left alone. FAT32 is refused: it cannot hold files
# over 4 GB.
#
#   1. stops curatables
#   2. makes the empty mount point immutable, so nothing can be written
#      to the SD card through it while the drive is unplugged
#   3. adds an /etc/fstab entry by UUID with nofail and mounts the drive
#      at /mnt/curatables-media (exFAT/NTFS get uid/gid/umask options,
#      since they have no Linux owners)
#   4. copies the existing videos/uploads/thumbnails/channels onto the
#      drive (skipped if the drive already holds a curatables-library),
#      checks the copy, and keeps the SD-card originals under
#      <data dir>/media.before-external-<date>
#   5. sets storage.media_path and storage.import_roots in config.json
#   6. installs a systemd drop-in with RequiresMountsFor= and
#      ReadWritePaths= for the mount
#   7. starts curatables again
#
# This script never formats anything.
#
# Environment overrides: CURATABLES_DATA (/home/curatables/curatables-data),
# CURATABLES_USER (curatables), CURATABLES_MEDIA_MOUNT (/mnt/curatables-media).

set -euo pipefail

DATA="${CURATABLES_DATA:-/home/curatables/curatables-data}"
SVC_USER="${CURATABLES_USER:-curatables}"
DROPIN_DIR=/etc/systemd/system/curatables.service.d
DROPIN="$DROPIN_DIR/external-drive.conf"

die() { echo "ERROR: $*" >&2; exit 1; }
say() { echo "==> $*"; }

[ "$(id -u)" -eq 0 ] || die "run as root (sudo $0 $*)"
MODE=whole
if [ "${1:-}" = "--media-only" ]; then
    MODE=media
    shift
fi
[ $# -eq 1 ] || die "usage: $0 [--media-only] /dev/<partition>   (see: lsblk -f)"
DEV="$1"
[ -b "$DEV" ] || die "$DEV is not a block device. List drives with: lsblk -f"

FSTYPE="$(blkid -o value -s TYPE "$DEV" || true)"
UUID="$(blkid -o value -s UUID "$DEV" || true)"

ROOT_SRC="$(findmnt -n -o SOURCE /)"
case "$ROOT_SRC" in "$DEV"*) die "$DEV holds the root filesystem; pick the external drive";; esac

id -u "$SVC_USER" >/dev/null 2>&1 || die "service user $SVC_USER does not exist"

if [ "$MODE" = media ]; then
    # shellcheck source=scripts/setup-media-drive.sh
    . "$(dirname "$(readlink -f "$0")")/setup-media-drive.sh"
    exit 0
fi

[ -n "$UUID" ] || die "$DEV has no filesystem. Format it first: sudo mkfs.ext4 -L curatables $DEV (erases it)"
[ "$FSTYPE" = "ext4" ] || die "$DEV is $FSTYPE; moving the whole library needs ext4. Keep the database on the SD card instead with: sudo $0 --media-only $DEV   (or reformat, which erases it: sudo mkfs.ext4 -L curatables $DEV)"

if findmnt -n "$DATA" >/dev/null 2>&1; then
    CURRENT="$(findmnt -n -o SOURCE "$DATA")"
    [ "$CURRENT" = "$DEV" ] && grep -q "UUID=$UUID" /etc/fstab \
        && { say "$DEV is already mounted at $DATA; nothing to do."; exit 0; }
    die "$DATA is already a mount point ($CURRENT). Unmount it first."
fi

EXISTING_MNT="$(findmnt -n -o TARGET --source "$DEV" || true)"
[ -z "$EXISTING_MNT" ] || die "$DEV is mounted at $EXISTING_MNT. Unmount it first: sudo umount $EXISTING_MNT"

say "Stopping curatables"
systemctl stop curatables.service 2>/dev/null || true

TMP_MNT="$(mktemp -d)"
trap 'mountpoint -q "$TMP_MNT" && umount "$TMP_MNT"; rmdir "$TMP_MNT" 2>/dev/null || true' EXIT
mount "$DEV" "$TMP_MNT"

if [ -f "$TMP_MNT/config.json" ] || [ -f "$TMP_MNT/db/curatables.db" ]; then
    say "The drive already holds a curatables library; using it as is."
elif [ -d "$DATA" ] && [ -n "$(ls -A "$DATA" 2>/dev/null)" ]; then
    NEED_KB="$(du -sk "$DATA" | cut -f1)"
    FREE_KB="$(df -Pk "$TMP_MNT" | awk 'NR==2 {print $4}')"
    [ "$FREE_KB" -gt "$NEED_KB" ] || die "drive has ${FREE_KB} KB free, the library needs ${NEED_KB} KB"
    say "Copying $DATA to the drive (this can take a while)"
    cp -a "$DATA"/. "$TMP_MNT"/
    sync
else
    say "No existing library found; the drive starts empty."
fi

chown -R "$SVC_USER:$SVC_USER" "$TMP_MNT"
chmod 0750 "$TMP_MNT"
umount "$TMP_MNT"

if [ -d "$DATA" ] && [ -n "$(ls -A "$DATA" 2>/dev/null)" ]; then
    KEEP="$DATA.before-external-$(date +%Y%m%d-%H%M%S)"
    say "Keeping the old SD-card copy at $KEEP (delete it once you are happy)"
    mv "$DATA" "$KEEP"
fi
install -d -o "$SVC_USER" -g "$SVC_USER" "$DATA"

if ! grep -q "UUID=$UUID" /etc/fstab; then
    say "Adding /etc/fstab entry"
    cp /etc/fstab "/etc/fstab.before-curatables-$(date +%Y%m%d-%H%M%S)"
    printf 'UUID=%s %s ext4 defaults,noatime,nofail,x-systemd.device-timeout=30s 0 2\n' \
        "$UUID" "$DATA" >> /etc/fstab
fi

systemctl daemon-reload
mount "$DATA"
chown "$SVC_USER:$SVC_USER" "$DATA"

say "Installing $DROPIN"
install -d "$DROPIN_DIR"
cat > "$DROPIN" <<'EOF'
[Service]
Environment=CURATABLES_REQUIRE_MOUNT=1
EOF
systemctl daemon-reload

say "Starting curatables"
systemctl start curatables.service
say "Done. The library now lives on $DEV (UUID $UUID), mounted at $DATA."
