# Curatables — Backup & Restore

> **tl;dr** — a permanent install (`install.sh --systemd` or the Pi
> image) backs up the database and `config.json` every night at 03:00
> into `/var/lib/curatables-backups`, keeping 14 days. On the Pi image,
> **Box → Drives & backups** makes a backup now, restores one, and can
> keep copies on the videos drive. Elsewhere, restore with
> `scripts/restore.sh <snapshot.db>` after stopping the server.

## From the dashboard (Pi image)

**Box → Drives & backups** has **Back up now**, a **Restore** button for
each snapshot (it stops the server, runs `restore.sh` as the service
user and starts it again), and, when the videos are on a USB drive,
**Also keep backups on the videos drive**. With that on, each nightly
snapshot is also copied to `<media folder>/backups` on the drive, so the
library survives the SD card dying (`storage.drive_backups` in
`config.json`).

The rest of this page explains what is backed up and how to back up and restore from a terminal.


## What's at stake

`db/curatables.db` in the data dir is **the entire curation history**:
every approved video, kid profile, channel, custom title, tag,
bookmark, reaction, comment, and usage event. `config.json` next to it
holds the parent password hash, the session secret and every setting.
These two files are what the nightly backup copies.

Downloaded video files can usually be fetched again from their source
URL. Not always: a video the uploader deleted is gone, which is one
reason to mark favourites **keep forever**.

## What the backup does not cover

- `videos/<id>/video.*` — downloaded videos. Re-downloadable while the
  source still exists.
- `uploads/<id>/video.*` — videos uploaded by you or the kids. These are
  originals and can't be re-fetched.
- `thumbnails/` — thumbnails you or the kids picked.
- `channels/<id>/{banner,icon}.*` — kid channel art.

For a full copy, stop the server and copy the whole data dir (see
"Full copy" below).

## Where backups go

| Install | Data dir | Backups |
|---|---|---|
| `install.sh --systemd`, Pi image | `/home/curatables/curatables-data` | `/var/lib/curatables-backups` |
| try-out (`.venv/bin/python run.py`) | `~/curatables-data` | `~/curatables-data/backups` (manual runs) |

`/var/lib/curatables-backups` is on the system disk (the SD card on a
Pi), next to the database. If the videos are on a USB drive, turn on
**Also keep backups on the videos drive** (Box → Drives & backups), or
losing the SD card loses the database and its backups together.

## Manual backup (one-off)

```bash
# systemd install
sudo -u curatables CURATABLES_DATA_DIR=/home/curatables/curatables-data \
    /opt/curatables/scripts/backup.sh /var/lib/curatables-backups

# try-out install
./scripts/backup.sh                      # → ~/curatables-data/backups/
./scripts/backup.sh /media/usb/backups/  # → custom output dir
```

The script uses SQLite's online backup API through Python's built-in
`sqlite3` module (no extra package needed). It is safe to run while the
server is processing requests and checks the snapshot with
`PRAGMA integrity_check`.

**Do NOT** just `cp curatables.db elsewhere` while the server runs. A
live WAL database needs its `-wal` and `-shm` siblings copied
atomically; `cp` races with the writer.

Each run writes `curatables-YYYY-MM-DD-HHMMSS.db` and
`curatables-YYYY-MM-DD-HHMMSS.config.json` (mode 0600, it contains the
password hash and session secret). The newest 14 pairs are kept
(`RETAIN=N` to change). The script never fills the card it writes to: it
stops with an error when the output folder would keep less than
`MIN_FREE_MB` (default 1024) plus the database's size free, and the
dashboard warns when the SD card is low.

## Daily automated backup (systemd timer)

`install.sh --systemd` and the Pi image install and enable
`curatables-backup.timer`. Check it with:

```bash
systemctl list-timers curatables-backup.timer
journalctl -u curatables-backup -n 20
ls -l /var/lib/curatables-backups
```

## Off-device copies (recommended)

The timer protects against mistakes and a corrupted database, not
against losing the whole box. Copy the backups elsewhere now and then:

- **USB stick**:
  ```bash
  sudo rsync -a /var/lib/curatables-backups/ /media/usb/curatables-backups/
  ```
- **Another machine on the LAN**: `scp`/`rsync` over SSH.
- **Cloud storage** (Backblaze B2, S3): `rclone sync` on a second timer.

## Restore

Stop the server first (a running writer corrupts the database
mid-swap), and run the restore as the user that owns the data dir so
file ownership stays right:

```bash
sudo systemctl stop curatables
sudo -u curatables CURATABLES_DATA_DIR=/home/curatables/curatables-data \
    /opt/curatables/scripts/restore.sh /var/lib/curatables-backups/curatables-2026-04-12-030000.db
sudo systemctl start curatables
```

`restore.sh`:

1. checks the snapshot with `PRAGMA integrity_check` before touching
   anything;
2. refuses to run if it sees a running server;
3. keeps the current files as `*.pre-restore-YYYYMMDD-HHMMSS` so you
   can undo the restore;
4. restores the matching `.config.json` too (password and settings),
   with `storage.path` set to the target data dir. Set `SKIP_CONFIG=1`
   to restore only the database.

## Full copy (videos and uploads included)

```bash
sudo systemctl stop curatables
sudo rsync -a /home/curatables/curatables-data/ /media/usb/curatables-data-full/
sudo systemctl start curatables
```

## Testing that your backups actually work

A backup you've never restored from is a hope, not a backup. Every few
months, start a throwaway server on a copy of the latest snapshot and
check you can log in and see your library:

```bash
LATEST="$(ls -1t /var/lib/curatables-backups/curatables-*.db | head -n1)"
TEST=/tmp/curatables-restore-test
rm -rf "$TEST" && mkdir -p "$TEST/db"
sudo cp "$LATEST" "$TEST/db/curatables.db"
sudo cp "${LATEST%.db}.config.json" "$TEST/config.json"
sudo chown -R "$(id -un)" "$TEST"
/opt/curatables/.venv/bin/python /opt/curatables/run.py --data-dir "$TEST" --port 8099
```

Open `http://curatables.local:8099/parent/`, log in with your usual
password, and check the profiles and the content list. Videos show as
missing files, which is expected: only the database and settings were
restored (with only a handful of videos, the test server may start
re-downloading them into the test folder; that's harmless). Stop it with
Ctrl-C and delete `/tmp/curatables-restore-test`.
