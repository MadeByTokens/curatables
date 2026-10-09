#!/bin/bash -e
# Host-side step: place the Wi-Fi setup template and the help README on the FAT boot partition
# so users can edit it from Windows/macOS/Linux before first boot. pi-gen
# copies ${ROOTFS_DIR}/boot/firmware/ onto the boot partition at export.

SRC="$(dirname "$(readlink -f "$0")")/files/curatables-wifi.txt"

install -d "${ROOTFS_DIR}/boot/firmware"
install -m 0644 "${SRC}" "${ROOTFS_DIR}/boot/firmware/curatables-wifi.txt"
install -m 0644 "$(dirname "${SRC}")/curatables-README.txt" "${ROOTFS_DIR}/boot/firmware/curatables-README.txt"
