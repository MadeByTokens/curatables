#!/usr/bin/env bash
#
# Curatables DB backup — WAL-safe snapshot via sqlite3 .backup.
#
# Usage:
#   ./scripts/backup.sh [OUT_DIR]
#
# Environment:
#   CURATABLES_DATA_DIR   Source data dir (default: $HOME/curatables-data)
#   RETAIN                Number of snapshots to keep (default: 14)
#
# Copies the live DB into $OUT_DIR/curatables-YYYY-MM-DD-HHMMSS.db using
# SQLite's online backup API — safe to run while the server is running,
# WAL file is handled transparently. `cp` is NOT safe for a live WAL DB.

set -euo pipefail

DATA_DIR="${CURATABLES_DATA_DIR:-$HOME/curatables-data}"
DB_PATH="$DATA_DIR/db/curatables.db"
OUT_DIR="${1:-$DATA_DIR/backups}"
RETAIN="${RETAIN:-14}"

if [ ! -f "$DB_PATH" ]; then
    echo "ERROR: DB not found at $DB_PATH" >&2
    exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
    echo "ERROR: sqlite3 not on PATH" >&2
    exit 1
fi

mkdir -p "$OUT_DIR"

TIMESTAMP="$(date +%Y-%m-%d-%H%M%S)"
OUT_FILE="$OUT_DIR/curatables-$TIMESTAMP.db"

# SQLite online backup — fully ACID, WAL-aware. `.backup` is a single
# atomic snapshot, better than a dump+restore for large DBs.
sqlite3 "$DB_PATH" ".backup '$OUT_FILE'"

# Integrity check on the fresh snapshot. Quick on small DBs.
if ! sqlite3 "$OUT_FILE" "PRAGMA integrity_check;" | grep -q '^ok$'; then
    echo "ERROR: integrity check failed on $OUT_FILE" >&2
    exit 2
fi

echo "Backed up to: $OUT_FILE ($(du -h "$OUT_FILE" | cut -f1))"

# Retention: keep the most recent $RETAIN snapshots, delete older ones.
if [ "$RETAIN" -gt 0 ]; then
    # shellcheck disable=SC2012
    # (intentional use of ls + tail — filenames here are timestamp-only,
    # no newlines or spaces possible)
    ls -1t "$OUT_DIR"/curatables-*.db 2>/dev/null \
        | tail -n "+$((RETAIN + 1))" \
        | xargs -r rm --
fi
