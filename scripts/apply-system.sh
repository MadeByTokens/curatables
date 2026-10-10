#!/bin/bash
# Install (or refresh) the Raspberry Pi image's system files from a
# Curatables code tree: systemd units, the wired-only network hook and
# automatic security updates. The image build runs it in its chroot, and
# the in-dashboard updater runs it after switching to a new version, so a
# new release can add or change services without re-flashing.
#
# Usage: apply-system.sh [CODE_DIR]     (default /opt/curatables, as root)
#
# Safe to run again: it only copies files and enables units. Drop-ins in
# /etc/systemd/system/curatables.service.d/ (the videos drive) are kept.

set -euo pipefail

ROOT="${1:-/opt/curatables}"
ETC="${CURATABLES_ETC:-/etc}"
[ -d "$ROOT/systemd" ] || { echo "ERROR: $ROOT has no systemd/ folder" >&2; exit 1; }

UNITS=(
	curatables.service
	curatables-updater.service curatables-updater.path
	curatables-backup.service curatables-backup.timer
	curatables-mdns-unicast.service
	curatables-helper.service
	curatables-recovery.service curatables-diagnose.service
	curatables-firstboot.service
)
ENABLE=(
	curatables.service
	curatables-updater.path
	curatables-backup.timer
	curatables-mdns-unicast.service
	curatables-helper.service
	curatables-recovery.service curatables-diagnose.service
	curatables-firstboot.service
)

install -d "$ETC/systemd/system"
for unit in "${UNITS[@]}"; do
	install -m 0644 "$ROOT/systemd/$unit" "$ETC/systemd/system/$unit"
done

install -d "$ETC/NetworkManager/dispatcher.d"
install -m 0755 "$ROOT/scripts/nm-wired-only.sh" \
	"$ETC/NetworkManager/dispatcher.d/70-curatables-wired-only"

# Debian security updates install by themselves every day (the
# unattended-upgrades package, part of the image). Everything else waits
# for the parent's "Update system packages" button.
install -d "$ETC/apt/apt.conf.d"
printf 'APT::Periodic::Update-Package-Lists "1";\nAPT::Periodic::Unattended-Upgrade "1";\n' \
	> "$ETC/apt/apt.conf.d/20auto-upgrades"

if [ "${CURATABLES_SKIP_SYSTEMCTL:-0}" != "1" ]; then
	if [ -d /run/systemd/system ]; then
		systemctl daemon-reload
	fi
	systemctl enable "${ENABLE[@]}" >/dev/null 2>&1 || systemctl enable "${ENABLE[@]}"
fi
echo "System files installed from $ROOT"
