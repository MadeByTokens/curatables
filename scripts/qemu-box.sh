#!/bin/bash
# Boots the Curatables Raspberry Pi image in QEMU on a laptop, to try a
# release (or a local build) as a whole system without touching a real
# box: systemd, the server, the helper, first-boot setup, backups.
#
#   scripts/qemu-box.sh                  latest release image from GitHub
#   scripts/qemu-box.sh --image FILE     an .img or .img.xz (e.g. pi-gen's)
#   scripts/qemu-box.sh --fresh          throw away what the box saved
#
# Then open http://localhost:8080/ (SSH, once turned on in setup:
# ssh -p 2223 admin@localhost). Ctrl-A X stops it.
#
# How: QEMU's generic arm64 machine with Debian's arm64 cloud kernel, as
# the Pi kernel needs Pi hardware. The image is decompressed and readied
# once (scripts/qemu-box-prepare.sh, in a throwaway privileged Docker
# container, so no root password); every boot writes to a copy-on-write
# layer on top, which --fresh discards: the next boot is a just-flashed
# box again. Wi-Fi, USB drives and the Pi's power readings don't exist
# here. Needs qemu-system-aarch64 (qemu-system-arm), qemu-img, docker,
# xz, curl, and gh for the release download. Emulated, so it is several
# times slower than a Pi 4: first boot takes a few minutes.
set -euo pipefail

REPO=MadeByTokens/curatables
CLOUD_URL=https://cloud.debian.org/images/cloud/trixie/latest
CLOUD_IMG=debian-13-genericcloud-arm64.qcow2
DIR=${CURATABLES_QEMU_DIR:-$HOME/.cache/curatables-qemu}
HERE=$(cd "$(dirname "$0")" && pwd)

image="" fresh=0 port=8080 ssh_port=2223 cpus=4 mem=2048 size=16G
while [ $# -gt 0 ]; do
    case "$1" in
        --image) image=$2; shift ;;
        --fresh) fresh=1 ;;
        --port) port=$2; shift ;;
        --ssh-port) ssh_port=$2; shift ;;
        --cpus) cpus=$2; shift ;;
        --mem) mem=$2; shift ;;
        -h|--help) sed -n '2,21p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done

for tool in qemu-system-aarch64 qemu-img docker xz curl; do
    command -v "$tool" >/dev/null || { echo "Needs $tool." >&2; exit 1; }
done
mkdir -p "$DIR"

if [ -z "$image" ]; then
    command -v gh >/dev/null || { echo "Needs gh to fetch the release, or pass --image." >&2; exit 1; }
    tag=$(gh release view -R "$REPO" --json tagName -q .tagName)
    image="$DIR/curatables-${tag#v}-raspberry-pi.img.xz"
    if [ ! -f "$image" ]; then
        echo "Downloading the $tag image..."
        (cd "$DIR" && gh release download "$tag" -R "$REPO" -p "$(basename "$image")*" --clobber \
            && sha256sum -c "$(basename "$image").sha256")
    fi
fi
[ -f "$image" ] || { echo "No image at $image" >&2; exit 1; }

if [ ! -f "$DIR/$CLOUD_IMG" ]; then
    echo "Downloading Debian's arm64 cloud image (for its kernel)..."
    curl -sSfL -o "$DIR/$CLOUD_IMG.part" "$CLOUD_URL/$CLOUD_IMG"
    (cd "$DIR" && curl -sSfL "$CLOUD_URL/SHA512SUMS" | grep " $CLOUD_IMG\$" \
        | sed "s/$CLOUD_IMG\$/$CLOUD_IMG.part/" | sha512sum -c --quiet)
    mv "$DIR/$CLOUD_IMG.part" "$DIR/$CLOUD_IMG"
fi

# One readied base per image; the box's own changes go in box.qcow2.
name=$(basename "$image"); name=${name%.xz}; name=${name%.img}
base="$DIR/base-$name.img"
work="$DIR/work-$name"
if [ ! -f "$base" ]; then
    echo "Readying $name (once)..."
    rm -rf "$work"; mkdir -p "$work"
    case "$image" in
        *.xz) xz -dc "$image" > "$work/box.img" ;;
        *) cp --sparse=always "$image" "$work/box.img" ;;
    esac
    truncate -s "$size" "$work/box.img"
    qemu-img convert -O raw "$DIR/$CLOUD_IMG" "$work/cloud.img"
    docker run --rm --privileged -v /dev:/dev -v "$work:/work" \
        -v "$HERE/qemu-box-prepare.sh:/prepare.sh:ro" \
        -e DEBIAN_FRONTEND=noninteractive -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        debian:trixie sh /prepare.sh
    rm -f "$work/cloud.img"
    mv "$work/kernel/vmlinuz" "$base.vmlinuz"
    mv "$work/kernel/initrd.img" "$base.initrd"
    mv "$work/box.img" "$base"
    rm -rf "$work"
    fresh=1
fi

overlay="$DIR/box-$name.qcow2"
if [ "$fresh" = 1 ] || [ ! -f "$overlay" ]; then
    rm -f "$overlay"
    qemu-img create -q -f qcow2 -b "$base" -F raw "$overlay"
    echo "Fresh box: like a just-flashed card."
fi

for p in "$port" "$ssh_port"; do
    if (exec 3<>"/dev/tcp/127.0.0.1/$p") 2>/dev/null; then
        echo "Port $p is already in use on this computer; pick another with --port or --ssh-port." >&2
        exit 1
    fi
done

echo "Starting. Curatables: http://localhost:$port/  (Ctrl-A X stops it)"
exec qemu-system-aarch64 \
    -M virt -cpu cortex-a72 -smp "$cpus" -m "$mem" \
    -kernel "$base.vmlinuz" -initrd "$base.initrd" \
    -append "root=/dev/vda2 rootfstype=ext4 rw console=ttyAMA0 net.ifnames=0" \
    -drive "file=$overlay,format=qcow2,if=virtio" \
    -netdev "user,id=net0,hostfwd=tcp:127.0.0.1:$port-:80,hostfwd=tcp:127.0.0.1:$ssh_port-:22" \
    -device virtio-net-pci,netdev=net0 \
    -device virtio-rng-pci \
    -nographic
