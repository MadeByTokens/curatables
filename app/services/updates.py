"""yt-dlp updates through a privileged systemd helper.

The service is sandboxed (``ProtectSystem=strict``, ``NoNewPrivileges``;
only the data directory is writable), so it can neither ``pip install``
into its venv nor restart itself. Instead it drops a request flag in the
data dir; the root-owned ``curatables-updater.path`` unit runs
``scripts/updater.sh`` (``scripts/ytdlp_update.py``: download the newest
yt-dlp beside the one in use, check it, switch only if it works,
restart the service), writes a result file and removes the request.
The work runs on the box, so a parent leaving the page changes nothing.

This module is the unprivileged half. Files, directly under the data dir:
  - ``update-request.json``   written by the app, removed by the helper.
  - ``update-progress.json``  the step under way, while it runs.
  - ``update-result.json``    written by the helper, read by the app:
    ``status`` "ok" (updated, or already the newest), "kept" (the new
    version failed its checks, the one in use stays) or "error".
  - ``update-auto.json``      when the nightly check last looked.

The nightly check (``maybe_auto_update``, run from the app's own loop)
drops the same request between 1 and 3 in the morning, family time,
when a newer yt-dlp is out, so the box keeps up with YouTube's changes
without a parent noticing broken downloads first.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional

from app.config import Config
from app.services import safe_fetch
from app.services.family_time import family_zone, valid_timezone

logger = logging.getLogger(__name__)

REQUEST_FILENAME = "update-request.json"
RESULT_FILENAME = "update-result.json"
PROGRESS_FILENAME = "update-progress.json"
AUTO_FILENAME = "update-auto.json"

# Installed by install.sh --systemd and the Pi image. Without it nothing
# would act on a request, so the nightly check stays quiet.
UPDATER_UNIT = Path("/etc/systemd/system/curatables-updater.path")
# Family time: after bedtime, done well before the 04:00 system restart.
AUTO_HOURS = (1, 2)
# Look once a night; when GitHub can't be asked, try the update itself
# once a week (it finds out on its own whether there is a newer one).
AUTO_EVERY = timedelta(hours=20)
AUTO_BLIND_EVERY = timedelta(days=7)

# updater.sh rejects any other kind.
KIND_YTDLP = "yt-dlp"
VALID_KINDS = (KIND_YTDLP,)


def request_path(config: Config) -> Path:
    return config.data_dir / REQUEST_FILENAME


def result_path(config: Config) -> Path:
    return config.data_dir / RESULT_FILENAME


def ytdlp_version() -> Optional[str]:
    """Installed yt-dlp version string, or None if it can't be read."""
    try:
        import yt_dlp  # local: keeps importing this module cheap
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


def request_update(config: Config, kind: str = KIND_YTDLP, auto: bool = False) -> None:
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
            "auto": auto,
        },
    )


def pending_request(config: Config) -> Optional[dict]:
    """The outstanding request flag, or None. Present == helper not done."""
    path = request_path(config)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def last_result(config: Config) -> Optional[dict]:
    """The most recent result the helper wrote, or None if never run."""
    path = result_path(config)
    if not path.exists():
        return None
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def progress(config: Config) -> str:
    """The step the updater is on ("Checking the new version"), or ""."""
    try:
        with open(config.data_dir / PROGRESS_FILENAME) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return ""
    stage = data.get("stage") if isinstance(data, dict) else ""
    return stage if isinstance(stage, str) else ""


def update_state(config: Config) -> dict:
    """Bundle everything the settings page needs to render the Updates box."""
    pending = pending_request(config)
    return {
        "current_version": ytdlp_version(),
        "pending": pending,
        "progress": progress(config) if pending else "",
        "result": last_result(config),
    }


_LATEST_URL = "https://api.github.com/repos/yt-dlp/yt-dlp/releases/latest"
_latest_cache: dict = {}


def latest_ytdlp_version(ttl: float = 3600) -> Optional[str]:
    """The newest yt-dlp release tag, or None if it cannot be checked.
    Cached for an hour so the diagnostics page stays quick."""
    now = time.monotonic()
    if _latest_cache and now - _latest_cache["at"] < ttl:
        cached: Optional[str] = _latest_cache["version"]
        return cached
    version = None
    try:
        body = safe_fetch.fetch_public(_LATEST_URL, max_bytes=1024 * 1024, timeout=6,
                                       headers={"Accept": "application/vnd.github+json"})
        tag = json.loads(body).get("tag_name")
        version = str(tag) if tag else None
    except Exception:
        version = None
    _latest_cache.update(at=now, version=version)
    return version


def _version_key(version: str) -> tuple:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def _parse_time(text: object) -> Optional[datetime]:
    if not isinstance(text, str):
        return None
    try:
        when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return when if when.tzinfo else when.replace(tzinfo=timezone.utc)


def _last_auto_check(config: Config) -> Optional[datetime]:
    try:
        with open(config.data_dir / AUTO_FILENAME) as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    return _parse_time(data.get("checked_at")) if isinstance(data, dict) else None


def maybe_auto_update(config: Config, now: Optional[datetime] = None,
                      latest: Callable[[], Optional[str]] = latest_ytdlp_version) -> bool:
    """Ask for a downloader update when it is night for the family and a
    newer yt-dlp is out. True when a request was dropped. The updater
    tries the new version before switching, as for the button."""
    if not config.server.auto_update_downloader or not UPDATER_UNIT.exists():
        return False
    if not valid_timezone(config.server.timezone):
        return False  # the box runs on UTC: its "night" can be the family's evening
    now = now or datetime.now(timezone.utc)
    if now.astimezone(family_zone(config.server.timezone)).hour not in AUTO_HOURS:
        return False
    if pending_request(config) is not None:
        return False
    last = _last_auto_check(config)
    if last is not None and timedelta(0) <= now - last < AUTO_EVERY:
        return False
    _atomic_write_json(config.data_dir / AUTO_FILENAME, {"checked_at": now.isoformat()})
    current = ytdlp_version()
    newest = latest()
    if newest and current:
        due = _version_key(newest) > _version_key(current)
    else:
        result = last_result(config) or {}
        finished = _parse_time(result.get("finished_at"))
        due = finished is None or now - finished >= AUTO_BLIND_EVERY
    if not due:
        logger.info("Nightly check: yt-dlp %s is the newest; nothing to do", current or "?")
        return False
    logger.info("Nightly check: yt-dlp %s is installed, %s is out; updating the downloader",
                current or "?", newest or "maybe newer")
    request_update(config, auto=True)
    return True
