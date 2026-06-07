"""Tests for the structured-JSON logging path.

Covers: format auto-resolution from env var, the JSON formatter
shape (timestamp / level / logger / message), request_id pass-
through, exception traceback inclusion, and that ``extra={...}``
fields are surfaced as top-level keys.
"""

from __future__ import annotations

import json
import logging
import os

import pytest

from app.logging_config import (
    JsonFormatter, _resolve_format, _build_formatter, setup_logging,
)


def _make_record(name: str = "curatables.test",
                 level: int = logging.INFO,
                 msg: str = "hello",
                 args: tuple = (),
                 exc_info=None,
                 extra: dict | None = None) -> logging.LogRecord:
    """Hand-build a LogRecord rather than going through a real
    logger so tests don't fight the global logging state."""
    record = logging.LogRecord(
        name=name, level=level, pathname=__file__, lineno=10,
        msg=msg, args=args, exc_info=exc_info,
    )
    if extra:
        for k, v in extra.items():
            setattr(record, k, v)
    return record


class TestResolveFormat:
    def test_explicit_arg_wins(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "json")
        assert _resolve_format("plain") == "plain"

    def test_env_fallback(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "json")
        assert _resolve_format(None) == "json"

    def test_default_plain(self, monkeypatch):
        monkeypatch.delenv("LOG_FORMAT", raising=False)
        assert _resolve_format(None) == "plain"

    def test_case_insensitive(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "JSON")
        assert _resolve_format(None) == "json"


class TestBuildFormatter:
    def test_plain_returns_classic_formatter(self):
        f = _build_formatter("plain")
        assert isinstance(f, logging.Formatter)
        assert not isinstance(f, JsonFormatter)

    def test_json_returns_json_formatter(self):
        assert isinstance(_build_formatter("json"), JsonFormatter)


class TestJsonFormatterShape:
    def test_required_fields_present(self):
        rec = _make_record()
        out = JsonFormatter().format(rec)
        body = json.loads(out)
        assert body["level"] == "INFO"
        assert body["logger"] == "curatables.test"
        assert body["message"] == "hello"
        assert "timestamp" in body
        assert body["timestamp"].endswith("+00:00")  # UTC ISO 8601

    def test_message_args_are_interpolated(self):
        rec = _make_record(msg="hi %s", args=("world",))
        body = json.loads(JsonFormatter().format(rec))
        assert body["message"] == "hi world"

    def test_request_id_included_when_set(self):
        rec = _make_record(extra={"request_id": "abc123"})
        body = json.loads(JsonFormatter().format(rec))
        assert body["request_id"] == "abc123"

    def test_request_id_dash_placeholder_omitted(self):
        """RequestIDLogFilter writes "-" when no request is in flight;
        suppress that in the JSON output to avoid noise."""
        rec = _make_record(extra={"request_id": "-"})
        body = json.loads(JsonFormatter().format(rec))
        assert "request_id" not in body

    def test_exception_info_attached(self):
        try:
            raise RuntimeError("boom")
        except RuntimeError:
            import sys
            rec = _make_record(level=logging.ERROR, msg="failed",
                               exc_info=sys.exc_info())
        body = json.loads(JsonFormatter().format(rec))
        assert "exc" in body
        assert "RuntimeError: boom" in body["exc"]

    def test_extra_fields_surface_as_top_level_keys(self):
        rec = _make_record(extra={"profile_id": 42, "video_id": "yt_abc"})
        body = json.loads(JsonFormatter().format(rec))
        assert body["profile_id"] == 42
        assert body["video_id"] == "yt_abc"

    def test_unjsonable_extra_falls_back_to_repr(self):
        class _NotSerializable:
            def __repr__(self):
                return "<NotSerializable>"
        rec = _make_record(extra={"obj": _NotSerializable()})
        body = json.loads(JsonFormatter().format(rec))
        assert body["obj"] == "<NotSerializable>"


class TestSetupLoggingHonorsFormat:
    def test_env_var_switches_handlers_to_json(self, monkeypatch, tmp_path):
        monkeypatch.setenv("LOG_FORMAT", "json")
        setup_logging("info", log_dir=tmp_path / "logs")
        try:
            root = logging.getLogger()
            for handler in root.handlers:
                assert isinstance(handler.formatter, JsonFormatter), (
                    f"handler {handler!r} did not get the JSON formatter"
                )
        finally:
            # Restore the test session's default plain formatter so we
            # don't leak JSON logging into subsequent tests.
            monkeypatch.delenv("LOG_FORMAT", raising=False)
            setup_logging("info")

    def test_explicit_plain_overrides_env(self, monkeypatch):
        monkeypatch.setenv("LOG_FORMAT", "json")
        setup_logging("info", log_format="plain")
        try:
            for handler in logging.getLogger().handlers:
                assert not isinstance(handler.formatter, JsonFormatter)
        finally:
            monkeypatch.delenv("LOG_FORMAT", raising=False)
            setup_logging("info")
