#!/usr/bin/env bash
# Build the Curatables Raspberry Pi appliance image with pi-gen, in Docker.
#
# pi-gen runs the whole build (incl. an ARM chroot via qemu/binfmt) inside
# a Docker container, so this works on an x86_64 host as long as Docker
# and the qemu-aarch64 binfmt handler are present:
#
#   docker info >/dev/null            # Docker daemon reachable
#   ls /proc/sys/fs/binfmt_misc/qemu-aarch64   # ARM emulation registered
#       (install with: sudo apt install qemu-user-static binfmt-support,
#        or: docker run --privileged --rm tonistiigi/binfmt --install arm64)
#
# Output: pi-gen/.pi-gen/deploy/image_<date>-curatables.img.xz
#
# Env overrides:
#   PIGEN_DIR   where to clone pi-gen      (default: pi-gen/.pi-gen)
#   PIGEN_REF   pi-gen commit (or branch) to build (default: PIGEN_COMMIT)
#
# 64-bit images come from pi-gen's `arm64` branch, not from ARCH in
# config: pi-gen's build.sh hard-sets ARCH after sourcing config. Its
# default branch builds 32-bit armhf, for which dependencies such as
# curl_cffi may lack prebuilt wheels.

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
PIGEN_DIR="${PIGEN_DIR:-${HERE}/.pi-gen}"
# pi-gen's arm64 branch at the commit this release was built and tested
# with, so two builds of one commit start from the same base system. Move
# it on purpose, and test the image, to take a newer pi-gen.
PIGEN_COMMIT=d346cd53562b3c6b4f94c9155175f814258c456d
PIGEN_REF="${PIGEN_REF:-${PIGEN_COMMIT}}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found"
command -v git    >/dev/null 2>&1 || die "git not found"
docker info >/dev/null 2>&1 || die "docker daemon not reachable (need permission / running daemon)"
[ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ] || \
	die "qemu-aarch64 binfmt not registered — see header for how to install ARM emulation"

# pi-gen's build-docker.sh gates on `which qemu-aarch64` (the bare name).
# Ubuntu's qemu-user-static ships it as `qemu-aarch64-static`, so the gate
# fails even though the binfmt handler is registered and working. Provide a
# `qemu-aarch64` shim on PATH pointing at whatever static qemu exists. The
# actual ARM execution uses the kernel binfmt handler (which the container
# self-registers), not this binary — the shim only satisfies the name check.
if ! command -v qemu-aarch64 >/dev/null 2>&1; then
	SHIM="${HERE}/.binshim"
	mkdir -p "${SHIM}"
	for q in /usr/bin/qemu-aarch64-static /usr/libexec/qemu-binfmt/aarch64-binfmt-P /usr/bin/qemu-aarch64; do
		if [ -x "$q" ]; then ln -sf "$q" "${SHIM}/qemu-aarch64"; break; fi
	done
	[ -e "${SHIM}/qemu-aarch64" ] || die "no static qemu-aarch64 binary found to shim (install qemu-user-static)"
	export PATH="${SHIM}:${PATH}"
	say "Shimmed qemu-aarch64 -> $(readlink "${SHIM}/qemu-aarch64")"
fi

# 1. Fetch pi-gen at PIGEN_REF (an existing checkout is moved to it).
if [ ! -d "${PIGEN_DIR}/.git" ]; then
	[ ! -e "${PIGEN_DIR}" ] || die "${PIGEN_DIR} exists but is not a git checkout; remove it"
	git init -q "${PIGEN_DIR}"
	git -C "${PIGEN_DIR}" remote add origin https://github.com/RPi-Distro/pi-gen
fi
if [ "$(git -C "${PIGEN_DIR}" rev-parse HEAD 2>/dev/null || true)" != "${PIGEN_REF}" ]; then
	say "Fetching pi-gen ${PIGEN_REF}"
	git -C "${PIGEN_DIR}" fetch -q --depth 1 origin "${PIGEN_REF}"
	git -C "${PIGEN_DIR}" checkout -q --detach FETCH_HEAD
fi
say "pi-gen at $(git -C "${PIGEN_DIR}" rev-parse --short HEAD)"

# 2. Stage the committed source (HEAD, without pi-gen/) into the custom
#    stage. Only tracked files go in: never local notes, caches or build
#    leftovers, and uncommitted edits are not part of the image.
DEST="${HERE}/stage-curatables/01-install/files/curatables"
say "Staging curatables $(git -C "${REPO_ROOT}" rev-parse --short HEAD) into ${DEST}"
rm -rf "${DEST}"
mkdir -p "${DEST}"
git -C "${REPO_ROOT}" archive --format=tar HEAD -- . ':!pi-gen' | tar -x -C "${DEST}"

# 3. Install our config + stage into the pi-gen checkout.
say "Installing config + stage-curatables into pi-gen"
cp "${HERE}/config" "${PIGEN_DIR}/config"
rm -rf "${PIGEN_DIR}/stage-curatables"
cp -a "${HERE}/stage-curatables" "${PIGEN_DIR}/stage-curatables"
grep -hEv '^[[:space:]]*(#|$)' "${REPO_ROOT}/scripts/apt-packages.txt" "${HERE}/image-packages.txt" \
	> "${PIGEN_DIR}/stage-curatables/00-install-packages/00-packages"
# Don't also export the intermediate Lite image.
touch "${PIGEN_DIR}/stage2/SKIP_IMAGES"

# 4. Build in Docker.
# build-docker.sh refuses to start if a prior pigen_work container lingers
# (e.g. from an interrupted run). Remove it so re-runs are clean. Use
# CONTINUE=1 ./pi-gen/build.sh to instead resume an existing container.
if [ "${CONTINUE:-0}" != "1" ]; then
	docker rm -v -f pigen_work >/dev/null 2>&1 || true
fi
say "Starting pi-gen Docker build (this takes ~30-60 min and several GB)"
cd "${PIGEN_DIR}"
./build-docker.sh

say "Done. Image(s) under: ${PIGEN_DIR}/deploy/"
ls -lh "${PIGEN_DIR}/deploy/" 2>/dev/null || true
