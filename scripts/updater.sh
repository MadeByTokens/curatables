#!/usr/bin/env bash
# Curatables privileged updater: the root-side half of the in-app yt-dlp
# update button (see app/services/updates.py).
#
# The sandboxed app process cannot pip-install into its venv or restart
# itself, so it drops a request flag in the data dir. The
# curatables-updater.path systemd unit notices the flag and runs this
# script (as root, via curatables-updater.service). The work is in
# scripts/ytdlp_update.py, run with the system Python: it downloads the
# newest yt-dlp beside the one in use, checks it the way Curatables uses
# it (scripts/ytdlp-check.py, as the service user), and switches only to
# a version that passes, putting the previous one back if Curatables
# does not run well with it. The flag is removed when it is done.
#
# Paths come from the environment (set by the .service unit), with the
# default-install values as fallbacks:
#   CURATABLES_ROOT  checkout dir holding .venv/        (/opt/curatables)
#   CURATABLES_DATA  data dir holding the flag files    (/home/curatables/curatables-data)
#   CURATABLES_USER  service user that owns the data dir (curatables)

set -euo pipefail

ROOT="${CURATABLES_ROOT:-/opt/curatables}"
export CURATABLES_ROOT="$ROOT"
export CURATABLES_DATA="${CURATABLES_DATA:-/home/curatables/curatables-data}"
export CURATABLES_USER="${CURATABLES_USER:-curatables}"

exec /usr/bin/python3 -I "$ROOT/scripts/ytdlp_update.py"
