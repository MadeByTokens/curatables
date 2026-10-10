#!/usr/bin/env bash
#
# Curatables restore. Replaces the live DB (and config.json, if the
# backup has one) with a backup snapshot.
#
# Usage (systemd install):
#   sudo systemctl stop curatables
#   sudo -u curatables CURATABLES_DATA_DIR=/home/curatables/curatables-data \
#       /opt/curatables/scripts/restore.sh /var/lib/curatables-backups/curatables-YYYY-MM-DD-HHMMSS.db
#   sudo systemctl start curatables
#
# Run it as the user that owns the data dir so file ownership stays right.
#
# Environment:
#   CURATABLES_DATA_DIR   Target data dir (default: $HOME/curatables-data)
#   PYTHON                Python interpreter (default: python3)
#   SKIP_CONFIG=1         Restore only the database, keep the current config.json
#
# The server MUST be stopped first — restoring under a live DB corrupts
# the WAL. The script refuses to run if it sees a running server.

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-file.db>" >&2
    exit 1
fi

BACKUP_FILE="$1"
BACKUP_CONFIG="${BACKUP_FILE%.db}.config.json"
DATA_DIR="${CURATABLES_DATA_DIR:-$HOME/curatables-data}"
DB_PATH="$DATA_DIR/db/curatables.db"
CONFIG_PATH="$DATA_DIR/config.json"
PYTHON="${PYTHON:-python3}"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

if ! "$PYTHON" - "$BACKUP_FILE" <<'PYEOF'
import sqlite3, sys
conn = sqlite3.connect(f"file:{sys.argv[1]}?mode=ro", uri=True)
sys.exit(0 if conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok" else 1)
PYEOF
then
    echo "ERROR: backup failed integrity check — refusing to restore" >&2
    exit 2
fi

if pgrep -f "python.*run\.py" >/dev/null 2>&1; then
    echo "ERROR: a curatables server process appears to be running." >&2
    echo "Stop it first (sudo systemctl stop curatables) before restoring." >&2
    exit 3
fi

STAMP="$(date +%Y%m%d-%H%M%S)"
if [ -f "$DB_PATH" ]; then
    mv "$DB_PATH" "$DB_PATH.pre-restore-$STAMP"
    rm -f "$DB_PATH-wal" "$DB_PATH-shm"
fi
mkdir -p "$(dirname "$DB_PATH")"
cp "$BACKUP_FILE" "$DB_PATH"
echo "Restored database from: $BACKUP_FILE"

if [ "${SKIP_CONFIG:-0}" != "1" ] && [ -f "$BACKUP_CONFIG" ]; then
    if [ -f "$CONFIG_PATH" ]; then
        cp "$CONFIG_PATH" "$CONFIG_PATH.pre-restore-$STAMP"
    fi
    "$PYTHON" - "$BACKUP_CONFIG" "$DATA_DIR" <<'PYEOF'
import json, sys
src, data_dir = sys.argv[1:3]
with open(src) as f:
    cfg = json.load(f)
cfg.setdefault("storage", {})["path"] = data_dir
with open(f"{data_dir}/config.json", "w") as f:
    json.dump(cfg, f, indent=2)
PYEOF
    chmod 600 "$CONFIG_PATH"
    echo "Restored config.json from: $BACKUP_CONFIG"
fi

echo "Previous files (if any) kept with a .pre-restore-$STAMP suffix."
echo "You can now start the server."
