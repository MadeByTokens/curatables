#!/bin/sh
# Readies a copy of the Raspberry Pi image for QEMU's generic arm64
# machine. Run by qemu-box.sh as root inside a throwaway debian:trixie
# container, with the work folder mounted at /work holding:
#   box.img    a decompressed copy of the Pi image, already enlarged
#   cloud.img  Debian's arm64 cloud image as raw, for its kernel
# It grows the system partition to fill box.img (a real card does that on
# its first boot), copies the cloud kernel's modules into it, and leaves
# that kernel and its initrd in /work/kernel/ for QEMU's -kernel/-initrd.
set -eu

cd /work
apt-get -qq update >/dev/null
apt-get -qq install -y fdisk e2fsprogs >/dev/null

# Start sector of partition $2 of image $1.
part_start() {
    sfdisk -l -o Device,Start "$1" | awk -v dev="$1$2" '$1 == dev { print $2 }'
}

BOX=""
CLOUD=""
cleanup() {
    umount /mnt/box /mnt/cloud 2>/dev/null || true
    [ -z "$BOX" ] || losetup -d "$BOX"
    [ -z "$CLOUD" ] || losetup -d "$CLOUD"
}
trap cleanup EXIT

echo ", +" | sfdisk -N 2 --no-reread -q box.img
BOX=$(losetup -f --show -o $(($(part_start box.img 2) * 512)) box.img)
e2fsck -fy "$BOX" >/dev/null || [ $? -le 1 ]
resize2fs "$BOX" >/dev/null
CLOUD=$(losetup -r -f --show -o $(($(part_start cloud.img 1) * 512)) cloud.img)

mkdir -p /mnt/box /mnt/cloud kernel
mount "$BOX" /mnt/box
mount -o ro "$CLOUD" /mnt/cloud
version=$(ls /mnt/cloud/usr/lib/modules)
rm -rf "/mnt/box/usr/lib/modules/$version"
cp -a "/mnt/cloud/usr/lib/modules/$version" /mnt/box/usr/lib/modules/
cp "/mnt/cloud/boot/vmlinuz-$version" kernel/vmlinuz
cp "/mnt/cloud/boot/initrd.img-$version" kernel/initrd.img
chmod 644 kernel/*
chown -R "${HOST_UID:-0}:${HOST_GID:-0}" kernel
echo "kernel $version"
