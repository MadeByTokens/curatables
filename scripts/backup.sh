#!/usr/bin/env bash
#
# Curatables backup — WAL-safe snapshot of the database plus config.json.
#
# Usage:
#   ./scripts/backup.sh [OUT_DIR]
#
# Environment:
#   CURATABLES_DATA_DIR   Source data dir (default: $HOME/curatables-data)
#   RETAIN                Number of snapshots to keep (default: 14)
#   PYTHON                Python interpreter (default: python3)
#
# Writes $OUT_DIR/curatables-YYYY-MM-DD-HHMMSS.db (SQLite online backup
# API, safe while the server runs) and, next to it,
# curatables-YYYY-MM-DD-HHMMSS.config.json (password hash, session
# secret and every setting; mode 0600). Videos are not copied: downloads
# can be fetched again, uploads and custom pictures need a copy of the
# whole data dir (see docs/backup.md).

set -euo pipefail

DATA_DIR="${CURATABLES_DATA_DIR:-$HOME/curatables-data}"
DB_PATH="$DATA_DIR/db/curatables.db"
CONFIG_PATH="$DATA_DIR/config.json"
OUT_DIR="${1:-$DATA_DIR/backups}"
RETAIN="${RETAIN:-14}"
PYTHON="${PYTHON:-python3}"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: DB not found at $DB_PATH (set CURATABLES_DATA_DIR?)" >&2
    exit 1
fi

umask 077
mkdir -p "$OUT_DIR"

TIMESTAMP="$(date +%Y-%m-%d-%H%M%S)"
OUT_FILE="$OUT_DIR/curatables-$TIMESTAMP.db"

"$PYTHON" - "$DB_PATH" "$OUT_FILE" <<'PYEOF'
import sqlite3, sys
src_path, out_path = sys.argv[1:3]
src = sqlite3.connect(f"file:{src_path}?mode=ro", uri=True)
dst = sqlite3.connect(out_path)
with dst:
    src.backup(dst)
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
dst.close()
src.close()
if ok != "ok":
    sys.exit(f"ERROR: integrity check failed on {out_path}: {ok}")
PYEOF

if [ -f "$CONFIG_PATH" ]; then
    cp "$CONFIG_PATH" "$OUT_DIR/curatables-$TIMESTAMP.config.json"
fi

echo "Backed up to: $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"

# Retention: keep the most recent $RETAIN snapshots (and their config
# copies), delete older ones. Filenames are timestamp-only, so ls is safe.
prune() {
    [ "$RETAIN" -gt 0 ] || return 0
    # shellcheck disable=SC2012
    ls -1t "$1"/curatables-*.db 2>/dev/null \
        | tail -n "+$((RETAIN + 1))" \
        | while read -r old; do
            rm -f -- "$old" "${old%.db}.config.json"
        done
}
prune "$OUT_DIR"

# "Also keep backups on the videos drive" (Box, Drives): a second copy
# in <media folder>/backups, so the library survives the SD card dying.
DRIVE_DIR="$("$PYTHON" - "$CONFIG_PATH" <<'PYEOF' 2>/dev/null || true
import json, os, sys
try:
    storage = json.load(open(sys.argv[1])).get("storage", {})
except (OSError, ValueError):
    sys.exit(0)
media = storage.get("media_path") or ""
if storage.get("drive_backups") and media and os.path.isdir(media):
    print(os.path.join(media, "backups"))
PYEOF
)"
if [ -n "$DRIVE_DIR" ]; then
    if mkdir -p "$DRIVE_DIR" && cp "$OUT_FILE" "$DRIVE_DIR/" && {
            [ ! -f "$OUT_DIR/curatables-$TIMESTAMP.config.json" ] ||
            cp "$OUT_DIR/curatables-$TIMESTAMP.config.json" "$DRIVE_DIR/"; }; then
        echo "Copied to the videos drive: $DRIVE_DIR"
        prune "$DRIVE_DIR"
    else
        echo "WARNING: could not copy the backup to the videos drive ($DRIVE_DIR)" >&2
    fi
fi
