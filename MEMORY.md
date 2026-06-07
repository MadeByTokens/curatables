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

## pi-gen image build gotchas (pi-gen/)
- **Package lists must live in a SUB-stage dir**, never at the stage root.
  pi-gen's `run_stage` only descends into sub-stage directories; a loose
  `00-packages` at the stage root is silently ignored (→ chroot with no
  deps). Use `00-install-packages/00-packages`. Stage root only holds
  `prerun.sh`, `EXPORT_IMAGE`, `SKIP_IMAGES`.
- `build-docker.sh` **sources** `config` (not `--env-file`), so quoted
  values like `STAGE_LIST="stage0 stage1 ..."` are safe.
- Boot FAT partition (Windows/macOS-visible) is sourced from
  `${ROOTFS_DIR}/boot/firmware/` on bookworm — drop boot-partition files
  there (e.g. the Wi-Fi config template).
- aarch64 wheels exist for all our deps (curl_cffi, reportlab, pillow) —
  no `build-essential`/`libffi-dev` needed in the image. Validated in an
  `arm64v8/debian:bookworm` container (run with `--platform linux/arm64`).
- deno installs fine in the chroot with just `unzip` added; use
  `DENO_INSTALL=/usr/local` so the binary lands at `/usr/local/bin/deno`.

## Wi-Fi firstboot must run AFTER NetworkManager
`raspi-config nonint do_wifi_ssid_passphrase` / `do_wifi_country` drive
`nmcli` on bookworm and require NetworkManager **active**, else they fail
"No supported network connection manager found". So
`curatables-firstboot.service` orders `After=NetworkManager.service` (NOT
`Before=network-pre.target`). `nmcli device wifi connect` is idempotent
(reactivates the saved profile), so re-running each boot is safe.
Ethernet needs nothing — Pi OS DHCPs eth0 automatically.
