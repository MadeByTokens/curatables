#!/bin/bash -e
# Standard pi-gen stage preamble: start this stage's rootfs from a copy
# of the previous stage's output.
if [ ! -d "${ROOTFS_DIR}" ]; then
	copy_previous
fi
