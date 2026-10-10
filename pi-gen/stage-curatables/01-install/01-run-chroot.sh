#!/bin/bash -e
# In-chroot step: turn the copied source into a ready-to-run appliance.
#   - create the unprivileged 'curatables' service user + data dir
#   - build the venv and install Python deps (pulls a fresh yt-dlp)
#   - install Deno (yt-dlp's JS runtime for YouTube extraction)
#   - install + enable the systemd units and other system files
#     (scripts/apply-system.sh)
#   - acceptance-test with run.py --check
#
# First boot is handled by the OS + the app itself: Raspberry Pi OS
# expands the filesystem and brings up Avahi (curatables.local); the app
# ships with NO password, so the first visit to the dashboard is
# redirected to /parent/setup where the parent sets one. Nothing here
# bakes a default password.

ROOT=/opt/curatables
DATA=/home/curatables/curatables-data

# --- service user + data dir -------------------------------------------
if ! id -u curatables >/dev/null 2>&1; then
	useradd --system --create-home --home-dir /home/curatables \
		--shell /usr/sbin/nologin curatables
fi
install -d -o curatables -g curatables "${DATA}"

# --- python venv + deps ------------------------------------------------
python3 -m venv "${ROOT}/.venv"
"${ROOT}/.venv/bin/pip" install --upgrade pip
"${ROOT}/.venv/bin/pip" install -r "${ROOT}/requirements.txt"

# --- deno (system-wide, on PATH for the service) -----------------------
"${ROOT}/scripts/install-deno.sh" /usr/local

# --- system files (units, network hook, security updates) -------------
# The same script the dashboard's updater runs after an update.
"${ROOT}/scripts/apply-system.sh" "${ROOT}"

# --- acceptance test ---------------------------------------------------
# Non-fatal on deno/mDNS warnings; fails the build only if a REQUIRED dep
# is missing (run.py --check exits non-zero in that case).
"${ROOT}/.venv/bin/python" "${ROOT}/run.py" --check

# Code and venv stay root-owned: the service only needs to read them, and
# the root-run updater must never execute files the service user can edit.
chown -R root:root "${ROOT}"
