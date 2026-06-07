"""yt-dlp update orchestration via a privileged systemd helper.

The Curatables server runs sandboxed (see ``systemd/curatables.service``:
``ProtectSystem=strict``, ``NoNewPrivileges=true``, with the data
directory as the *only* writable path). That hardening means the running
process **cannot** ``pip install`` into its own venv, and **cannot**
restart itself. So an "update yt-dlp from the dashboard" button cannot do
the work directly without tearing the sandbox down.

Instead the app drops a small request flag into the data directory — the
one place it is allowed to write (it already writes ``config.json``
there). A separate, root-owned systemd path-unit
(``systemd/curatables-updater.path``) notices the flag, runs the
privileged work in ``scripts/updater.sh`` (pip-upgrade yt-dlp, restart
the service), writes a result file back into the data dir, and removes
the request. The dashboard reads the result file on its next render.

This module is the app-side half: it reports the installed yt-dlp
version, writes the request flag atomically, and reads back the pending
request and last result. It never shells out and never needs privileges.

File contract (all live directly under the data dir):
  - ``update-request.json``  written by the app, removed by the helper.
  - ``update-result.json``   written by the helper, read by the app.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from app.config import Config

REQUEST_FILENAME = "update-request.json"
RESULT_FILENAME = "update-result.json"

# The only update kind wired today. The flag schema carries `kind` so a
# future "app" self-update (git pull + backup + migrate) can reuse the
# same helper without changing the file contract.
KIND_YTDLP = "yt-dlp"
VALID_KINDS = (KIND_YTDLP,)


def request_path(config: Config) -> Path:
    return config.data_dir / REQUEST_FILENAME


def result_path(config: Config) -> Path:
    return config.data_dir / RESULT_FILENAME


def ytdlp_version() -> Optional[str]:
    """Installed yt-dlp version string, or None if it can't be read."""
    try:
        import yt_dlp  # noqa: WPS433 (local import: keep module import-cheap)

        return getattr(yt_dlp.version, "__version__", None)
    except Exception:
        return None


def _atomic_write_json(path: Path, payload: dict) -> None:
    """Write JSON atomically (temp file + rename), matching Config.save."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as f:
        json.dump(payload, f, indent=2)
        f.flush()
        os.fsync(f.fileno())
    tmp_path.replace(path)  # atomic on POSIX


def request_update(config: Config, kind: str = KIND_YTDLP) -> None:
    """Drop an update-request flag for the privileged helper to act on.

    Raises ValueError on an unknown kind so a typo never silently writes
    a flag the helper will reject.
    """
    if kind not in VALID_KINDS:
        raise ValueError(f"unknown update kind: {kind!r}")
    _atomic_write_json(
        request_path(config),
        {
            "kind": kind,
            "requested_at": datetime.now(timezone.utc).isoformat(),
            "from_version": ytdlp_version(),
        },
    )


def pending_request(config: Config) -> Optional[dict]:
    """The outstanding request flag, or None. Present == helper not done."""
    path = request_path(config)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def last_result(config: Config) -> Optional[dict]:
    """The most recent result the helper wrote, or None if never run."""
    path = result_path(config)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def update_state(config: Config) -> dict:
    """Bundle everything the settings page needs to render the Updates box."""
    return {
        "current_version": ytdlp_version(),
        "pending": pending_request(config),
        "result": last_result(config),
    }
