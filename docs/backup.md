# Curatables — Backup & Restore

> **tl;dr** — run `scripts/backup.sh` to snapshot the DB. Restore with
> `scripts/restore.sh <snapshot.db>` after stopping the server. A
> systemd timer in `systemd/` wires this as a daily job.

## What's at stake

`~/curatables-data/db/curatables.db` is **the entire curation history**:
every approved video, kid profile, channel (including banners/icons),
custom thumbnail override, tag, bookmark, reaction, comment, and usage
event. Downloaded video files can always be re-fetched from their
source URLs; the DB is the one thing you cannot reproduce.

If your deployment is on spinning rust or a consumer-grade SSD, a
single hardware failure takes the DB with it. Curatables can't mitigate
that — **you need off-device backups**.

## What you can afford to lose

- `videos/<id>/video.mp4` — re-downloadable from `video.original_url`
  via yt-dlp. The DB retains the URL; lose the file, re-fetch.
- `uploads/<id>/video.<ext>` — kid uploads. These **are** original
  content and can't be re-fetched. Back them up separately if they
  matter (tar the `uploads/` tree).
- `thumbnails/profiles/<id>/...` — per-kid custom thumbnails. Lose
  them, lose the customizations; the DB flag `has_custom_thumb=1`
  would then point at a missing file and the media route would fall
  back to the canonical thumbnail silently.
- `channels/<id>/{banner,icon}.*` — kid channel art. Same story.

The default backup script covers **just the DB**, which is the single
most valuable artifact. If you want full-fidelity backup including
uploads and customizations, tar the full `~/curatables-data/` tree.

## Manual backup (one-off)

```bash
./scripts/backup.sh                      # → $HOME/curatables-data/backups/
./scripts/backup.sh /mnt/usb/backups/    # → custom output dir
```

The script uses SQLite's `.backup` command, which is a proper online
snapshot (WAL-aware, handles concurrent readers/writers). It's safe to
run while the server is processing requests.

**Do NOT** just `cp curatables.db elsewhere`. A live WAL DB needs
`-wal` and `-shm` siblings copied atomically; `cp` races with the
writer and produces a corrupt snapshot ~1 time in 100. The sqlite3
backup API is the only correct way.

Each snapshot is named `curatables-YYYY-MM-DD-HHMMSS.db`. The script
keeps the most recent 14 (configurable via `RETAIN=N`).

## Daily automated backup (systemd timer)

```bash
# Copy the service + timer into systemd. Adjust paths in the .service
# file if your install doesn't live at /opt/curatables.
sudo cp systemd/curatables-backup.service /etc/systemd/system/
sudo cp systemd/curatables-backup.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now curatables-backup.timer
```

Verify:
```bash
systemctl list-timers curatables-backup.timer
# Next scheduled run, last run, etc.
```

The timer runs at 03:00 local time daily. Retention is 14 days in the
service unit's `Environment=RETAIN=14` — adjust to taste.

## Off-device rotation (recommended)

The systemd timer snapshots to disk; it doesn't protect against disk
failure. For that, pair it with anything that rotates the `backups/`
directory off the device:

- **USB stick**: rsync once a week:
  ```
  rsync -a --delete ~/curatables-data/backups/ /mnt/usb/curatables/
  ```
- **Another machine on the LAN**: scp / rsync over SSH.
- **Cloud storage** (Backblaze B2, S3): `rclone sync` on a second timer.

## Restore

The server **must** be stopped first (a running writer will corrupt
the DB mid-swap):

```bash
sudo systemctl stop curatables
./scripts/restore.sh ~/curatables-data/backups/curatables-2026-04-12-030000.db
sudo systemctl start curatables
```

`restore.sh` does three safety things:
1. Verifies the backup file passes `PRAGMA integrity_check` before
   touching the live DB.
2. Refuses to run if it detects a live server process (best-effort;
   rely on your process manager, but the check catches the common
   "I forgot to stop it" mistake).
3. Preserves the pre-restore DB as
   `curatables.db.pre-restore-YYYYMMDD-HHMMSS` so you can roll back
   the restore itself if it turns out to be the wrong backup.

## Testing that your backups actually work

A backup you've never restored from is a hope, not a backup. Every
few months:

```bash
# Dry-run on a sandbox copy — don't overwrite the live DB.
mkdir -p /tmp/curatables-restore-test
cp ~/curatables-data/backups/curatables-*-030000.db /tmp/curatables-restore-test/
sqlite3 /tmp/curatables-restore-test/curatables-*.db "PRAGMA integrity_check;"
sqlite3 /tmp/curatables-restore-test/curatables-*.db \
    "SELECT COUNT(*) FROM videos; SELECT COUNT(*) FROM profiles;"
```

Confirms the snapshot is readable and has the data you expect.
