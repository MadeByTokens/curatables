#!/bin/bash -e
# In-chroot step: turn the copied source into a ready-to-run appliance.
#   - create the unprivileged 'curatables' service user + data dir
#   - build the venv and install Python deps (pulls a fresh yt-dlp)
#   - install Deno (yt-dlp's JS runtime for YouTube extraction)
#   - install + enable the systemd units (server + updater watcher)
#   - point the service unit at the venv python
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
export DENO_INSTALL=/usr/local
curl -fsSL https://deno.land/install.sh | sh
# install.sh lands the binary at ${DENO_INSTALL}/bin/deno; ensure it's on
# the default PATH the service inherits.
if [ -x /usr/local/bin/deno ]; then
	:
elif [ -x /usr/local/deno ]; then
	ln -sf /usr/local/deno /usr/local/bin/deno
fi

# --- systemd units -----------------------------------------------------
cp "${ROOT}/systemd/curatables.service" /etc/systemd/system/
cp "${ROOT}/systemd/curatables-updater.service" /etc/systemd/system/
cp "${ROOT}/systemd/curatables-updater.path" /etc/systemd/system/

# The shipped unit runs system python on port 80; the appliance runs the
# venv python (where the deps live). Patch the ExecStart in place.
sed -i \
	's#^ExecStart=/usr/bin/python3 /opt/curatables/run.py#ExecStart=/opt/curatables/.venv/bin/python /opt/curatables/run.py#' \
	/etc/systemd/system/curatables.service

systemctl enable curatables.service
systemctl enable curatables-updater.path

# --- acceptance test ---------------------------------------------------
# Non-fatal on deno/mDNS warnings; fails the build only if a REQUIRED dep
# is missing (run.py --check exits non-zero in that case).
"${ROOT}/.venv/bin/python" "${ROOT}/run.py" --check

chown -R curatables:curatables "${ROOT}"
