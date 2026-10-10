#!/bin/bash -e
# Host-side step: put the Wi-Fi setup template and the help README on the
# FAT boot partition, which any computer can open before first boot.
# pi-gen copies ${ROOTFS_DIR}/boot/firmware/ onto it at export.

SRC="$(dirname "$(readlink -f "$0")")/files/curatables-wifi.txt"

install -d "${ROOTFS_DIR}/boot/firmware"
install -m 0644 "${SRC}" "${ROOTFS_DIR}/boot/firmware/curatables-wifi.txt"
install -m 0644 "$(dirname "${SRC}")/curatables-README.txt" "${ROOTFS_DIR}/boot/firmware/curatables-README.txt"
