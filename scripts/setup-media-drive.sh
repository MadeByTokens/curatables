# shellcheck shell=bash
# The --media-only half of setup-external-drive.sh, which sources this
# file after its argument checks. Expects DEV, FSTYPE, UUID, DATA,
# SVC_USER, DROPIN_DIR and the die/say helpers from that script.

MNT="${CURATABLES_MEDIA_MOUNT:-/mnt/curatables-media}"
LIB="$MNT/curatables-library"
CONFIG="$DATA/config.json"
MEDIA_DROPIN="$DROPIN_DIR/media-drive.conf"
MEDIA_SUBDIRS=(videos uploads thumbnails channels)

SVC_UID="$(id -u "$SVC_USER")"
SVC_GID="$(id -g "$SVC_USER")"

[ -n "$UUID" ] || die "$DEV has no filesystem the Pi can read (lsblk -f shows its type)."
case "$FSTYPE" in
    ext4)
        MOUNT_FS=ext4; PASS=2
        MOUNT_OPTS="defaults,noatime" ;;
    exfat)
        MOUNT_FS=exfat; PASS=0
        MOUNT_OPTS="uid=$SVC_UID,gid=$SVC_GID,fmask=0137,dmask=0027,noatime" ;;
    ntfs)
        MOUNT_FS=ntfs3; PASS=0
        MOUNT_OPTS="uid=$SVC_UID,gid=$SVC_GID,fmask=0137,dmask=0027,noatime" ;;
    vfat)
        MOUNT_FS=vfat; PASS=0
        MOUNT_OPTS="uid=$SVC_UID,gid=$SVC_GID,fmask=0137,dmask=0027,noatime,utf8"
        say "warning: $DEV is FAT32, which cannot hold files over 4 GB. Curatables skips anything bigger." ;;
    *)
        die "$DEV is '$FSTYPE'. The media drive must be exFAT, NTFS, ext4 or FAT32." ;;
esac
MOUNT_OPTS="$MOUNT_OPTS,nofail,x-systemd.device-timeout=30s"

EXISTING_MNT="$(findmnt -n -o TARGET --source "$DEV" | head -n1 || true)"
if [ -n "$EXISTING_MNT" ] && [ "$EXISTING_MNT" != "$MNT" ]; then
    die "$DEV is mounted at $EXISTING_MNT. Unmount it first: sudo umount $EXISTING_MNT"
fi
if mountpoint -q "$MNT"; then
    [ "$(findmnt -n -o SOURCE "$MNT")" = "$DEV" ] \
        || die "$MNT already has another drive mounted ($(findmnt -n -o SOURCE "$MNT"))."
fi

say "Stopping curatables"
systemctl stop curatables.service 2>/dev/null || true

if ! mountpoint -q "$MNT"; then
    install -d -m 0755 "$MNT"
    [ -z "$(ls -A "$MNT" 2>/dev/null)" ] \
        || die "$MNT is not empty, so files were written there without the drive. Move them away first."
    # With the drive unplugged, nothing may be created on the SD card
    # through the empty mount point.
    chattr +i "$MNT" 2>/dev/null || say "warning: could not make $MNT immutable"
fi

if ! grep -q "UUID=$UUID" /etc/fstab; then
    say "Adding /etc/fstab entry ($MOUNT_FS at $MNT)"
    cp /etc/fstab "/etc/fstab.before-curatables-$(date +%Y%m%d-%H%M%S)"
    printf 'UUID=%s %s %s %s 0 %s\n' "$UUID" "$MNT" "$MOUNT_FS" "$MOUNT_OPTS" "$PASS" >> /etc/fstab
fi
systemctl daemon-reload 2>/dev/null || true
if ! mountpoint -q "$MNT"; then
    mount "$MNT" || die "could not mount $DEV at $MNT ('dmesg | tail' says why). An NTFS drive Windows did not shut down cleanly must be checked in Windows first; an exFAT drive can be checked with: sudo fsck.exfat $DEV"
fi

if [ -d "$LIB" ] && [ -n "$(ls -A "$LIB" 2>/dev/null)" ]; then
    say "The drive already holds a curatables-library; using it as is."
else
    install -d -m 0750 "$LIB"
    NEED_KB=0
    for sub in "${MEDIA_SUBDIRS[@]}"; do
        [ -d "$DATA/$sub" ] && NEED_KB=$((NEED_KB + $(du -sk "$DATA/$sub" | cut -f1)))
    done
    FREE_KB="$(df -Pk "$LIB" | awk 'NR==2 {print $4}')"
    [ "$FREE_KB" -gt "$NEED_KB" ] || die "the drive has ${FREE_KB} KB free; the media needs ${NEED_KB} KB"
    for sub in "${MEDIA_SUBDIRS[@]}"; do
        [ -d "$DATA/$sub" ] || continue
        say "Copying $sub to the drive (this can take a while)"
        cp -R --preserve=timestamps "$DATA/$sub" "$LIB/"
        sync
        SRC_FILES="$(find "$DATA/$sub" -type f | wc -l)"
        DST_FILES="$(find "$LIB/$sub" -type f | wc -l)"
        SRC_BYTES="$(du -sb "$DATA/$sub" --exclude='.*' | cut -f1)"
        DST_BYTES="$(du -sb "$LIB/$sub" --exclude='.*' | cut -f1)"
        [ "$SRC_FILES" = "$DST_FILES" ] \
            || die "copy of $sub is incomplete ($DST_FILES of $SRC_FILES files); the SD-card copy is untouched"
        [ "$FSTYPE" != ext4 ] || [ "$SRC_BYTES" = "$DST_BYTES" ] \
            || die "copy of $sub differs in size; the SD-card copy is untouched"
    done
fi
# ext4 and ntfs3 both record who created a file, so what root copied
# above must be handed to the service user. exFAT has no owners; its
# mount options already give everything to the service user.
if [ "$FSTYPE" != exfat ]; then
    chown -R "$SVC_USER:$SVC_USER" "$LIB"
    chmod 0750 "$LIB"
fi

KEEP="$DATA/media.before-external-$(date +%Y%m%d-%H%M%S)"
for sub in "${MEDIA_SUBDIRS[@]}"; do
    if [ -d "$DATA/$sub" ]; then
        install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$KEEP"
        mv "$DATA/$sub" "$KEEP/"
    fi
done
[ -d "$KEEP" ] && say "Kept the SD-card copy at $KEEP (delete it once you are happy: sudo rm -r $KEEP)"

say "Pointing curatables at $LIB"
install -d -o "$SVC_USER" -g "$SVC_USER" -m 0750 "$DATA"
# As the service user: config.json lives in a folder that user controls.
runuser -u "$SVC_USER" -- python3 - "$CONFIG" "$DATA" "$LIB" "$MNT" <<'PY'
import json
import os
import sys

path, data, lib, mnt = sys.argv[1:]
cfg = {}
if os.path.exists(path):
    with open(path) as f:
        cfg = json.load(f)
storage = cfg.setdefault("storage", {})
storage.setdefault("path", data)
storage["media_path"] = lib
roots = storage.get("import_roots") or []
if mnt not in roots:
    roots.append(mnt)
storage["import_roots"] = roots
cfg.setdefault("parent", {})
tmp = path + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    json.dump(cfg, f, indent=2)
    f.flush()
    os.fsync(f.fileno())
os.replace(tmp, path)
PY

say "Installing $MEDIA_DROPIN"
install -d "$DROPIN_DIR"
printf '[Service]\nReadWritePaths=-%s\n' "$MNT" > "$MEDIA_DROPIN"
systemctl daemon-reload 2>/dev/null || true

say "Starting curatables"
systemctl start curatables.service
say "Done. Videos now live on $DEV ($FSTYPE, UUID $UUID) in $LIB."
say "Other files on the drive are untouched; the parent Import page can add them."
case "$FSTYPE" in exfat|vfat) say "This drive has no journal: after an unplug or power cut, use Repair on the Drives page." ;; esac
true
