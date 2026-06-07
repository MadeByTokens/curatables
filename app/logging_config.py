"""Logging configuration for curatables server.

Two output formats are supported:

- **plain** (default) — human-readable, optimised for ``journalctl``
  on a self-hosted box where the operator reads the live log.
- **json** — one JSON object per line, opt-in via ``LOG_FORMAT=json``
  in the environment. Wire Curatables's logs into a structured
  pipeline (Loki, Promtail, Vector, an ELK box) by exporting that
  variable in the systemd unit and the logs become directly
  ingestible without a regex.

The formatter is selected once at startup; switching at runtime
would require restarting the process.
"""

import datetime
import json
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FORMAT = "%(asctime)s [%(request_id)s] %(levelname)s %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


# Standard LogRecord attributes — used by the JSON formatter to spot
# extra=... keyword arguments callers passed to logger.info(...) and
# include them in the JSON payload.
_LOGRECORD_DEFAULT_ATTRS = {
    "name", "msg", "args", "levelname", "levelno", "pathname",
    "filename", "module", "exc_info", "exc_text", "stack_info",
    "lineno", "funcName", "created", "msecs", "relativeCreated",
    "thread", "threadName", "processName", "process", "message",
    "asctime", "request_id",
}


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log record.

    Required fields: timestamp (ISO 8601 UTC), level, logger, message.
    Optional: request_id (when populated by RequestIDLogFilter), exc
    (formatted traceback for ``logger.exception(...)`` calls), plus
    any ``extra={...}`` the caller passed.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.datetime.fromtimestamp(
                record.created, datetime.timezone.utc
            ).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = getattr(record, "request_id", None)
        if rid and rid != "-":
            payload["request_id"] = rid
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Anything passed via logger.info("msg", extra={"k": v}) lives
        # on the record as a regular attribute. Surface those in the
        # JSON payload so structured log readers can index them.
        for key, value in record.__dict__.items():
            if key in _LOGRECORD_DEFAULT_ATTRS or key.startswith("_"):
                continue
            try:
                json.dumps(value)
            except (TypeError, ValueError):
                value = repr(value)
            payload[key] = value
        return json.dumps(payload, default=str)


def _resolve_format(explicit: str | None) -> str:
    """Pick the active log format. Explicit arg > env > default plain."""
    if explicit:
        return explicit.lower()
    env = os.environ.get("LOG_FORMAT", "").strip().lower()
    return env or "plain"


def _build_formatter(fmt: str) -> logging.Formatter:
    if fmt == "json":
        return JsonFormatter()
    return logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)


def setup_logging(log_level: str = "info",
                  log_dir: Path | None = None,
                  log_format: str | None = None) -> None:
    """Configure logging with console and optional rotating file handler.

    Args:
        log_level: One of debug, info, warning, error.
        log_dir: Directory for log files. If provided, creates a rotating
                 file handler at {log_dir}/curatables.log (5MB, 3 backups).
        log_format: ``"plain"`` or ``"json"``. When omitted, falls back
                 to the ``LOG_FORMAT`` environment variable, then to
                 ``"plain"``.
    """
    from app.middleware.request_id import RequestIDLogFilter

    level = getattr(logging, log_level.upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(level)

    # Clear any existing handlers (prevents duplicate logs on reload)
    root.handlers.clear()

    fmt = _resolve_format(log_format)
    formatter = _build_formatter(fmt)
    rid_filter = RequestIDLogFilter()

    # Console handler
    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(formatter)
    console.addFilter(rid_filter)
    root.addHandler(console)

    # File handler (if log_dir provided)
    if log_dir:
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "curatables.log"
        file_handler = RotatingFileHandler(
            log_path, maxBytes=5 * 1024 * 1024, backupCount=3,
        )
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        file_handler.addFilter(rid_filter)
        root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("watchfiles").setLevel(logging.WARNING)
