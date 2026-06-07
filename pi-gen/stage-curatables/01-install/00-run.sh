#!/bin/bash -e
# Host-side step: copy the curatables source tree into the image rootfs.
# The tree was staged into files/curatables by pi-gen/build.sh (an rsync
# of this checkout, minus .git/.venv/data), so the image matches the
# exact code you built from — no network clone, works on unmerged
# branches.

SRC="$(dirname "$(readlink -f "$0")")/files/curatables"

install -d "${ROOTFS_DIR}/opt"
rm -rf "${ROOTFS_DIR}/opt/curatables"
cp -a "${SRC}" "${ROOTFS_DIR}/opt/curatables"
