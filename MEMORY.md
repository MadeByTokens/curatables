# Project memory — curatables

Append-only notes on non-obvious decisions, gotchas, and patterns.

## In-app updates must use a privileged helper, not direct pip
The systemd service (`systemd/curatables.service`) is hardened:
`ProtectSystem=strict` + `ReadWritePaths=<data dir>` means the running
app can write **only** its data dir — the venv is read-only to it — and
`NoNewPrivileges=true` means it cannot `sudo`/`systemctl restart` itself.
So any "update from the dashboard" feature CANNOT pip-install or restart
directly. Pattern used (Q2, the yt-dlp "Update" button):
- app drops `update-request.json` in the data dir (`app/services/updates.py`)
- root-owned `curatables-updater.path` → `curatables-updater.service`
  runs `scripts/updater.sh` (pip-upgrade yt-dlp, write
  `update-result.json`, rm flag, `systemctl restart curatables`)
- dashboard reads the result file on next render.
Don't weaken the sandbox to add self-update — use the decoupled helper.

## yt-dlp vs curatables are different update problems
yt-dlp: high churn (YouTube changes), low risk → safe to bump liberally.
curatables itself: forward-only migrator, **no downgrade** → any app
self-update MUST run `scripts/backup.sh` first. The updater only handles
yt-dlp today; `kind` field in the flag leaves room for an `app` kind later.

## Test client bypasses CSRF
Existing POST route tests (e.g. `tests/test_parent_library_toggle.py`)
POST without a `csrf_token` and still get 200 — the TestClient is exempt
from `CSRFMiddleware`. So new route tests don't need to fetch a token.
