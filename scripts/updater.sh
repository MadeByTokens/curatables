#!/usr/bin/env bash
# Curatables privileged updater — the root-side half of the in-app update
# button. See app/services/updates.py for the full rationale.
#
# The sandboxed app process cannot pip-install into its venv or restart
# itself, so it drops a request flag in the data dir. The
# curatables-updater.path systemd unit notices the flag and runs THIS
# script (as root, via curatables-updater.service). It:
#   1. reads the request flag
#   2. pip-upgrades yt-dlp in the venv
#   3. writes a result file the app reads back
#   4. removes the request flag (re-arming the path watcher)
#   5. restarts the curatables service so the new yt-dlp is live
#
# It deliberately does ONE narrow thing. The only update kind wired today
# is "yt-dlp"; anything else is rejected. It never runs arbitrary input
# from the flag — only the fixed pip command below.
#
# Paths come from the environment (set by the .service unit), with the
# default-install values as fallbacks:
#   CURATABLES_ROOT  checkout dir holding .venv/        (/opt/curatables)
#   CURATABLES_DATA  data dir holding the flag files    (/home/curatables/curatables-data)
#   CURATABLES_USER  service user that owns the data dir (curatables)

set -euo pipefail

ROOT="${CURATABLES_ROOT:-/opt/curatables}"
DATA="${CURATABLES_DATA:-/home/curatables/curatables-data}"
SVC_USER="${CURATABLES_USER:-curatables}"

REQ="$DATA/update-request.json"
RES="$DATA/update-result.json"
PIP="$ROOT/.venv/bin/pip"
PY="$ROOT/.venv/bin/python"

# Nothing to do if the flag vanished between the path trigger and now.
[[ -f "$REQ" ]] || exit 0

now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

ytdlp_ver() {
    "$PY" -c 'import yt_dlp; print(yt_dlp.version.__version__)' 2>/dev/null || echo ""
}

# Write the result file atomically (temp + mv) and hand ownership to the
# service user so the app can read it back through ReadWritePaths.
write_result() {
    local status="$1" message="$2" from_v="$3" to_v="$4"
    local tmp="$RES.tmp"
    "$PY" - "$tmp" "$status" "$message" "$from_v" "$to_v" "$(now)" <<'PYEOF'
import json, sys
tmp, status, message, from_v, to_v, finished_at = sys.argv[1:7]
with open(tmp, "w") as f:
    json.dump({
        "kind": "yt-dlp",
        "status": status,
        "message": message,
        "from_version": from_v or None,
        "to_version": to_v or None,
        "finished_at": finished_at,
    }, f, indent=2)
PYEOF
    mv -f "$tmp" "$RES"
    chown "$SVC_USER":"$SVC_USER" "$RES" 2>/dev/null || true
    chmod 0644 "$RES"
}

# Parse the requested kind without trusting it as code.
kind="$("$PY" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("kind",""))' "$REQ" 2>/dev/null || echo "")"

if [[ "$kind" != "yt-dlp" ]]; then
    write_result "error" "unsupported update kind: ${kind:-<none>}" "" ""
    rm -f "$REQ"
    exit 0
fi

before="$(ytdlp_ver)"
log="$(mktemp)"
trap 'rm -f "$log"' EXIT

if "$PIP" install --upgrade 'yt-dlp>=2024.0' >"$log" 2>&1; then
    after="$(ytdlp_ver)"
    if [[ "$before" == "$after" ]]; then
        write_result "ok" "Already up to date." "$before" "$after"
    else
        write_result "ok" "Updated." "$before" "$after"
    fi
    rm -f "$REQ"
    # Restart so the running server imports the new yt-dlp. Done after the
    # result is written so the restarted app shows the outcome.
    systemctl restart curatables || true
else
    # Surface the last few lines of pip output — enough to diagnose
    # without dumping the whole log into the dashboard.
    tail="$(tail -n 5 "$log" | tr '\n' ' ')"
    write_result "error" "pip upgrade failed: $tail" "$before" "$before"
    rm -f "$REQ"
    exit 1
fi
