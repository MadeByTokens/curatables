"""Tests for the yt-dlp update request/result file contract.

These cover the app-side half (app/services/updates.py): writing the
request flag, reading it back, reading a helper-written result, and the
bundled state the settings page renders. The privileged helper
(scripts/updater.sh) is a root-only systemd unit and is not exercised
here; what's tested is the file contract both sides agree on.
"""

import json

import pytest

from app.config import Config
from app.services import updates


@pytest.fixture
def config(tmp_path):
    c = Config()
    c.storage.path = str(tmp_path)
    return c


class TestRequest:
    def test_request_writes_flag(self, config):
        assert updates.pending_request(config) is None
        updates.request_update(config, kind=updates.KIND_YTDLP)

        req = updates.pending_request(config)
        assert req is not None
        assert req["kind"] == "yt-dlp"
        assert "requested_at" in req
        # The flag lands exactly where the path-unit watches.
        assert updates.request_path(config).name == "update-request.json"

    def test_request_is_valid_json_on_disk(self, config):
        updates.request_update(config)
        with open(updates.request_path(config)) as f:
            data = json.load(f)
        assert data["kind"] == "yt-dlp"

    def test_unknown_kind_rejected(self, config):
        with pytest.raises(ValueError):
            updates.request_update(config, kind="app")  # not wired yet
        assert updates.pending_request(config) is None


class TestResult:
    def test_last_result_none_when_absent(self, config):
        assert updates.last_result(config) is None

    def test_reads_helper_written_result(self, config):
        # Simulate what scripts/updater.sh writes.
        config.data_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "kind": "yt-dlp", "status": "ok", "message": "Updated.",
            "from_version": "2024.1.1", "to_version": "2024.12.31",
            "finished_at": "2026-06-07T00:00:00Z",
        }
        with open(updates.result_path(config), "w") as f:
            json.dump(payload, f)

        result = updates.last_result(config)
        assert result["status"] == "ok"
        assert result["to_version"] == "2024.12.31"

    def test_corrupt_result_is_ignored(self, config):
        config.data_dir.mkdir(parents=True, exist_ok=True)
        updates.result_path(config).write_text("{not json")
        assert updates.last_result(config) is None


class TestState:
    def test_state_shape(self, config):
        state = updates.update_state(config)
        assert set(state) == {"current_version", "pending", "result"}
        # yt-dlp is a hard dependency, so a version string is expected.
        assert state["current_version"]
        assert state["pending"] is None

    def test_state_reflects_pending(self, config):
        updates.request_update(config)
        state = updates.update_state(config)
        assert state["pending"] is not None
        assert state["pending"]["kind"] == "yt-dlp"
