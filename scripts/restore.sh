#!/usr/bin/env bash
#
# Curatables DB restore. Replaces the live DB with a backup snapshot.
#
# Usage:
#   sudo systemctl stop curatables
#   ./scripts/restore.sh /path/to/curatables-YYYY-MM-DD-HHMMSS.db
#   sudo systemctl start curatables
#
# Environment:
#   CURATABLES_DATA_DIR   Target data dir (default: $HOME/curatables-data)
#
# The server MUST be stopped before running this — restoring to a live
# DB corrupts the WAL. This script checks for the PID of a running
# server process and refuses to proceed if one is detected (best-effort;
# use systemctl/your process manager to be certain).

set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <backup-file.db>" >&2
    exit 1
fi

BACKUP_FILE="$1"
DATA_DIR="${CURATABLES_DATA_DIR:-$HOME/curatables-data}"
DB_PATH="$DATA_DIR/db/curatables.db"

if [ ! -f "$BACKUP_FILE" ]; then
    echo "ERROR: backup file not found: $BACKUP_FILE" >&2
    exit 1
fi

# Sanity: check the backup is a valid SQLite file before touching the live DB.
if ! sqlite3 "$BACKUP_FILE" "PRAGMA integrity_check;" | grep -q '^ok$'; then
    echo "ERROR: backup failed integrity check — refusing to restore" >&2
    exit 2
fi

# Best-effort liveness check. Not foolproof — rely on your process
# manager to actually stop the server.
if pgrep -f "python.*run\.py" >/dev/null 2>&1; then
    echo "ERROR: a curatables server process appears to be running."
    echo "Stop it first (systemctl stop curatables, or kill the process)"
    echo "before running this script." >&2
    exit 3
fi

# Preserve the current DB as .pre-restore in case the operator wants it back.
if [ -f "$DB_PATH" ]; then
    mv "$DB_PATH" "$DB_PATH.pre-restore-$(date +%Y%m%d-%H%M%S)"
    # Also move WAL/SHM siblings if present (they'll be stale after restore).
    rm -f "$DB_PATH-wal" "$DB_PATH-shm"
fi

mkdir -p "$(dirname "$DB_PATH")"
cp "$BACKUP_FILE" "$DB_PATH"

echo "Restored from: $BACKUP_FILE"
echo "Previous DB (if any) preserved with .pre-restore-* suffix."
echo "You can now start the server."
