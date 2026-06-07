# Upgrading Curatables

Curatables is a self-hosted appliance: you run the box, you do the
upgrade. The good news is the upgrade path is short and forward-only —
the schema migrator handles the database, the dependency check handles
the Python side, and a `systemctl restart` puts the new version live.
You can also leap multiple versions in one go (v0.3 → v0.5 → v0.6 in a
single upgrade) without booting the intermediate releases. The
migrator walks every pending file in order; the test suite pins this
contract.

> **You always need a backup before upgrading.** The migrator is
> forward-only — there is no automatic downgrade if the new release
> turns out to be broken on your data. Take the backup first, every
> time.

---

## Updating yt-dlp from the dashboard (no command line)

yt-dlp is the engine that fetches videos, and the sites it talks to
(YouTube especially) change often — so yt-dlp is the one dependency you
will need to update regularly, independent of Curatables releases. You
can do it from **Settings → Updates** in the parent dashboard without
touching a terminal: click **Update yt-dlp now**, and the page tells you
the result on reload. The server restarts itself when the update lands.

### How it works (and why it needs a helper)

The Curatables service runs sandboxed (see
[`systemd/curatables.service`](../systemd/curatables.service):
`ProtectSystem=strict`, `NoNewPrivileges=true`) — by design it can write
only its data directory and cannot `pip install` into its own venv or
restart itself. So the button does **not** run pip directly. Instead:

1. The app drops a small `update-request.json` flag in the data dir.
2. A separate root-owned path-unit,
   [`curatables-updater.path`](../systemd/curatables-updater.path),
   notices the flag and runs
   [`scripts/updater.sh`](../scripts/updater.sh) (as root, outside the
   sandbox): it pip-upgrades yt-dlp, writes an `update-result.json` back,
   removes the flag, and restarts `curatables`.
3. The dashboard reads the result file and shows the old → new version.

The helper does exactly one narrow thing — upgrade yt-dlp — and only ever
acts on a flag the app wrote. It does **not** upgrade Curatables itself
(that path stays manual + backup-first; see below).

### Enabling it

The updater units install with the systemd path:

```bash
sudo ./scripts/install.sh --systemd
sudo systemctl enable --now curatables-updater.path
```

If you installed before this feature existed, copy the two new unit files
(`systemd/curatables-updater.{service,path}`) into
`/etc/systemd/system/`, `daemon-reload`, then `enable --now
curatables-updater.path`. If your checkout, data dir, or service user
differs from the defaults (`/opt/curatables`,
`/home/curatables/curatables-data`, `curatables`), edit the
`CURATABLES_ROOT`/`CURATABLES_DATA`/`CURATABLES_USER` lines in
`curatables-updater.service` and the watched `PathExists` in
`curatables-updater.path` to match.

On a manual / non-systemd install the button has no helper to act on —
update yt-dlp the old way: `pip install -U yt-dlp` in the venv.

---

## Quick checklist

```text
1. Take a backup       (docs/backup.md)
2. Pull / unpack new   (git pull or download the new release tarball)
3. Update Python deps  (pip install -r requirements.txt --upgrade)
4. Run the dep check   (python run.py --check)
5. Restart the service (systemctl restart curatables)
6. Confirm /healthz    (curl http://curatables.local/healthz)
```

If any step fails, stop and read the relevant section below before
moving on. Each step is idempotent — re-running it on a successful
state is safe.

---

## 1. Back up first

The schema migrator is forward-only; recovering from a migration that
went sideways means restoring the SQLite file and the on-disk videos
from a backup. Run the same backup script the systemd timer runs:

```bash
sudo /opt/curatables/scripts/backup.sh
```

(Or whatever path you installed Curatables to — see
[`docs/backup.md`](backup.md) for the full procedure, including
where the snapshots land and how to restore from one.)

If you have not configured backups yet, do that *before* upgrading.

---

## 2. Pull the new release

If you cloned the git repo:

```bash
cd /opt/curatables   # or wherever you installed
sudo -u curatables git fetch --tags
sudo -u curatables git checkout v0.6.0   # or `main`, or the tag you want
```

If you downloaded a release tarball, untar it over the existing
install directory. **Do not** untar over the data directory
(`~curatables/curatables-data/` by default) — the database, videos,
thumbnails, and uploads live there and the tarball does not touch
them.

---

## 3. Update Python dependencies

The pinned requirements file enumerates every Python package
Curatables uses, with upper bounds on the next major. New releases
sometimes raise minimums or add packages (e.g. v0.6 added
`prometheus_client` for `/metrics`).

```bash
sudo -u curatables /opt/curatables/.venv/bin/pip install \
    -r /opt/curatables/requirements.txt --upgrade
```

`--upgrade` honors the pin ranges in `requirements.txt`, so it will
not jump across an upper bound on its own. If a newer version of
something you trust *does* cross the bound, edit `requirements.txt`
deliberately and re-run the install.

System binaries (`ffmpeg`, `deno`) are managed by your distro's
package manager. Curatables does not pin those; the dependency
check (next step) will tell you if a major version was missed.

---

## 4. Run the dependency check

```bash
sudo -u curatables /opt/curatables/.venv/bin/python \
    /opt/curatables/run.py --check
```

This prints resolved versions for every Python package, every system
binary Curatables knows about, and exits non-zero if anything required
is missing. Anything in the `Warnings:` block is a degrade-gracefully
optional dep (mDNS discovery, PDF export, browser impersonation, etc.)
— note them, but they do not block the upgrade.

If `--check` errors, fix what it lists before restarting the service.
Booting Curatables with a broken environment leaves the previous
version's process running and a half-installed new version on disk.

---

## 5. Restart the service

```bash
sudo systemctl restart curatables
```

The schema migrator runs at startup. It walks every pending migration
under `app/db/migrations/` (in numbered order), commits each one in
its own transaction, and stamps the tracking table. A multi-version
leap (e.g. you skipped v0.4 and went straight to v0.5) is handled
the same way — every pending migration applies in one boot.

`journalctl -u curatables -n 50 --no-pager` shows the migrator's
output. Successful boot looks like:

```
INFO app.db.migrator: Applying migration 0004_safety_verdicts
INFO app.db.migrator: Applying migration 0005_metrics_session
INFO app.db.schema: Applied 2 migration(s).
INFO curatables: server starting on http://0.0.0.0:80
```

If the migrator throws, the transaction rolls back — the database is
left at whichever version *succeeded*, not corrupted halfway through.
At that point: stop the service, inspect the traceback, and either
fix forward (a small `.py` migration file) or restore from your
pre-upgrade backup.

---

## 6. Confirm the upgrade landed

Curatables ships an unauthenticated `/healthz` probe that reports the
running version, process uptime, and a fresh DB SELECT result. After
the restart:

```bash
curl -s http://curatables.local/healthz | python3 -m json.tool
```

Healthy output:

```json
{
  "status": "ok",
  "version": "0.6.0",
  "uptime_seconds": 4.213,
  "db": "ok"
}
```

Things to verify:

- `version` matches the release you just installed.
- `status` is `"ok"`. A `"degraded"` reply (with HTTP 503) means the
  process is up but the DB query failed — check the logs and the data
  directory permissions.
- `uptime_seconds` is small (single-digit seconds), confirming the
  restart actually happened and you are not still talking to the old
  process.

You are also free to point any monitoring (uptime-kuma,
Prometheus blackbox, a curl-and-grep cron) at `/healthz`. If you have
opted into `/metrics`, the equivalent Prometheus check is
`curatables_uptime_seconds`.

---

## Troubleshooting

**The migrator stops with `OperationalError`.**
Inspect the traceback. SQLite errors are usually about a missing or
mistyped column. The DB is left at the last *successful* version, so
re-running the migrator after fixing the bad migration file is safe.

**`pip install` complains about an upper bound.**
A pinned dep wants a version above what `requirements.txt` allows.
Read the failing package's release notes, decide if you want to raise
the bound, edit `requirements.txt`, and re-run `pip install`. Don't
pass `--ignore-installed` or `--force-reinstall` — those mask real
incompatibilities.

**`systemctl restart` fails with `Address already in use`.**
A previous instance didn't shut down cleanly. `systemctl status
curatables` and `ss -tlnp | grep :80` show what is on the port.
Usually `systemctl stop curatables`, wait a second, then start again.

**`/healthz` responds 503 with `db: "error: ..."`.**
The process is up but the DB query failed. Common causes: the data
directory was renamed without updating the systemd unit, the SQLite
file is owned by the wrong user after a manual backup-restore, or
the disk is full. The error class in the JSON body points at which
of those it is.

**The new version started but a feature you used is missing.**
Compare against the [Roadmap](../README.md#roadmap) — features
sometimes land behind a config flag. If the README says the feature
shipped and your install does not have it, file an issue with your
`/healthz` output, the systemd unit, and the relevant log lines.

---

## Multi-version leaps

You do not have to step through every minor release on the way to a
target. Going from v0.3 directly to v0.6 runs every migration filed
between v0.3's last migration and v0.6's, in order, in one boot.
`tests/test_migrator.py::TestMigratorMultiVersionLeap` pins this.
The data is untouched between migrations except by what each
migration writes itself.

Caveats:

- Optional dependencies that became required in an intermediate
  version still need to be installed before booting the new one
  (e.g. `prometheus_client` from v0.6). The dependency check in
  step 4 catches this.
- Filesystem layout changes (rare) ship with the migration that
  introduces them. The migrator runs them as Python files
  (`NNNN_slug.py`) when needed; you do not run them by hand.

---

## Cross-references

- [`docs/backup.md`](backup.md) — backup + restore script and
  systemd timer.
- [`docs/deployment.md`](deployment.md) — first-time install and
  systemd unit.
- [`docs/dependencies.md`](dependencies.md) — canonical bill of
  materials with rationale per package.
- [`README.md`](../README.md) — Roadmap and Current Status.
