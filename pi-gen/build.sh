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
# Output: pi-gen/.pi-gen/deploy/curatables-<date>-arm64.img.xz
#
# Env overrides:
#   PIGEN_DIR   where to clone pi-gen      (default: pi-gen/.pi-gen)
#   PIGEN_REF   pi-gen branch to build     (default: bookworm)

set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "${HERE}/.." && pwd)"
PIGEN_DIR="${PIGEN_DIR:-${HERE}/.pi-gen}"
PIGEN_REF="${PIGEN_REF:-bookworm}"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

command -v docker >/dev/null 2>&1 || die "docker not found"
command -v rsync  >/dev/null 2>&1 || die "rsync not found"
docker info >/dev/null 2>&1 || die "docker daemon not reachable (need permission / running daemon)"
[ -e /proc/sys/fs/binfmt_misc/qemu-aarch64 ] || \
	die "qemu-aarch64 binfmt not registered — see header for how to install ARM emulation"

# 1. Fetch pi-gen.
if [ ! -d "${PIGEN_DIR}" ]; then
	say "Cloning pi-gen (${PIGEN_REF}) into ${PIGEN_DIR}"
	git clone --depth 1 --branch "${PIGEN_REF}" \
		https://github.com/RPi-Distro/pi-gen "${PIGEN_DIR}"
else
	say "Reusing existing pi-gen checkout at ${PIGEN_DIR}"
fi

# 2. Stage this checkout's source into the custom stage. Excludes build
#    artifacts and, crucially, the staging/clone dirs to avoid recursion.
DEST="${HERE}/stage-curatables/01-install/files/curatables"
say "Staging curatables source into ${DEST}"
rm -rf "${DEST}"
mkdir -p "${DEST}"
rsync -a \
	--exclude '.git' \
	--exclude '.venv' \
	--exclude '__pycache__' \
	--exclude '.pytest_cache' \
	--exclude '.coverage' \
	--exclude 'curatables-data' \
	--exclude 'pi-gen' \
	"${REPO_ROOT}/" "${DEST}/"

# 3. Install our config + stage into the pi-gen checkout.
say "Installing config + stage-curatables into pi-gen"
cp "${HERE}/config" "${PIGEN_DIR}/config"
rm -rf "${PIGEN_DIR}/stage-curatables"
cp -a "${HERE}/stage-curatables" "${PIGEN_DIR}/stage-curatables"
# Don't also export the intermediate Lite image.
touch "${PIGEN_DIR}/stage2/SKIP_IMAGES"

# 4. Build in Docker.
say "Starting pi-gen Docker build (this takes ~30-60 min and several GB)"
cd "${PIGEN_DIR}"
./build-docker.sh

say "Done. Image(s) under: ${PIGEN_DIR}/deploy/"
ls -lh "${PIGEN_DIR}/deploy/" 2>/dev/null || true
