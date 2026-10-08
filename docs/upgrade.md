# Upgrading Curatables

There are two separate things to keep up to date:

- **yt-dlp**, the engine that fetches videos. Sites like YouTube change
  often, so it needs updating every few weeks. One button in the
  dashboard does it.
- **Curatables itself**. Upgrade when a new version has something you
  want. One command does it, with a backup first.

> **The database only moves forward.** A new version may change the
> database layout, and there is no automatic way back. The upgrade
> command below takes a backup before it changes anything; keep it.

---

## Updating yt-dlp from the dashboard

Open **Settings → Updates** and click **Update yt-dlp now**. The server
restarts itself when the update is done; reload the page to see the old
→ new version.

### How it works

The Curatables service is sandboxed
([`systemd/curatables.service`](../systemd/curatables.service):
`ProtectSystem=strict`, `NoNewPrivileges=true`). It can write only its
data dir, so it cannot `pip install` or restart itself. Instead:

1. The app drops `update-request.json` in the data dir.
2. The root-owned path unit
   [`curatables-updater.path`](../systemd/curatables-updater.path)
   notices it and runs [`scripts/updater.sh`](../scripts/updater.sh).
   The script upgrades yt-dlp (running pip as the owner of the venv),
   writes `update-result.json`, removes the request and restarts
   `curatables`. Everything it reads or writes in the data dir it does
   as the service user, so a compromised app can't trick it into
   touching other files.
3. The dashboard reads the result file.

The updater only ever upgrades yt-dlp, to the newest release
(`yt-dlp>=2024.0`, no upper bound: yt-dlp changes fast and old versions
stop working).

`install.sh --systemd` and the Pi image install and enable it. On a
try-out install (`.venv/bin/python run.py`) there is no helper; run
`.venv/bin/pip install -U yt-dlp` and restart.

---

## Upgrading Curatables

On a permanent install (`install.sh --systemd` **or** the Pi image):

```bash
# First time only: get the source onto the box
git clone https://github.com/MadeByTokens/curatables.git ~/curatables

# Every upgrade
cd ~/curatables
git pull
sudo scripts/install.sh --systemd
```

`install.sh --systemd` on a running install:

1. takes a backup into `/var/lib/curatables-backups` and stops if the
   backup fails;
2. copies the new code to `/opt/curatables` and updates the Python
   packages in `/opt/curatables/.venv`;
3. runs `run.py --check`;
4. reinstalls the systemd units and restarts the server.

The database migrations run when the server starts. You can upgrade
across several versions in one go: every pending migration in
`app/db/migrations/` runs in order, each in its own transaction.

### Check it worked

```bash
journalctl -u curatables -n 50 --no-pager
curl -s http://curatables.local/healthz
```

The log shows any `Applying migration NNNN_...` lines followed by
`curatables server starting on http://0.0.0.0:80`. `/healthz` returns
`{"status": "ok", "version": "...", "uptime_seconds": ..., "db": "ok"}`;
a small `uptime_seconds` confirms the restart happened.

### If something goes wrong

**The migrator stops with an error.** Each migration runs in a
transaction, so the database stays at the last migration that worked.
Read the traceback in the journal. To go back to the old version,
check out the previous release, run `sudo scripts/install.sh --systemd`
again, then restore the pre-upgrade backup
([backup.md](backup.md#restore)).

**`run.py --check` fails.** It lists what's missing. Fix that and run
the install again; the old version keeps running until the restart.

**`/healthz` answers 503 with `db: "error: ..."`.** The server is up but
can't read the database. Usual causes: the data dir is owned by the
wrong user after a manual restore
(`sudo chown -R curatables:curatables /home/curatables/curatables-data`),
the disk is full, or the external drive is not mounted.

**`Address already in use` on restart.** An old process didn't exit.
`sudo systemctl stop curatables`, wait a second, start it again.

---

## Cross-references

- [backup.md](backup.md) — backups, restore, testing a backup.
- [deployment.md](deployment.md) — first-time install, systemd units,
  external drive.
- [dependencies.md](dependencies.md) — every package and binary.
