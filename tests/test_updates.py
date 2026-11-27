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
            updates.request_update(config, kind="app")
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
        assert set(state) == {"current_version", "pending", "progress", "result"}
        # yt-dlp is a hard dependency, so a version string is expected.
        assert state["current_version"]
        assert state["pending"] is None

    def test_state_reflects_pending(self, config):
        updates.request_update(config)
        state = updates.update_state(config)
        assert state["pending"] is not None
        assert state["pending"]["kind"] == "yt-dlp"


class TestNightly:
    """The nightly check drops the same request the button does, at night
    for the family, when a newer yt-dlp is out."""

    from datetime import datetime, timezone
    NIGHT = datetime(2026, 11, 26, 16, 30, tzinfo=timezone.utc)  # 01:30 in Tokyo
    DAY = datetime(2026, 11, 27, 4, 0, tzinfo=timezone.utc)      # 13:00 in Tokyo

    @pytest.fixture
    def nightly(self, config, tmp_path, monkeypatch):
        unit = tmp_path / "curatables-updater.path"
        unit.write_text("")
        monkeypatch.setattr(updates, "UPDATER_UNIT", unit)
        monkeypatch.setattr(updates, "ytdlp_version", lambda: "2026.10.21")
        config.server.timezone = "Asia/Tokyo"
        return config

    def test_asks_at_night_when_a_newer_version_is_out(self, nightly):
        assert updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: "2026.11.23")
        req = updates.pending_request(nightly)
        assert req["kind"] == "yt-dlp" and req["auto"] is True

    def test_not_until_the_family_time_zone_is_known(self, nightly):
        nightly.server.timezone = ""
        assert not updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: "2026.11.23")

    def test_not_in_the_day(self, nightly):
        assert not updates.maybe_auto_update(nightly, self.DAY, latest=lambda: "2026.11.23")
        assert updates.pending_request(nightly) is None

    def test_not_when_already_the_newest(self, nightly):
        assert not updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: "2026.10.21")
        assert not updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: "2026.09.01")

    def test_not_when_switched_off(self, nightly):
        nightly.server.auto_update_downloader = False
        assert not updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: "2026.11.23")

    def test_not_without_the_updater(self, nightly, tmp_path, monkeypatch):
        monkeypatch.setattr(updates, "UPDATER_UNIT", tmp_path / "missing")
        assert not updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: "2026.11.23")

    def test_not_while_one_is_running(self, nightly):
        updates.request_update(nightly)
        assert not updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: "2026.11.23")
        assert updates.pending_request(nightly)["auto"] is False

    def test_looks_once_a_night(self, nightly):
        from datetime import timedelta
        asked = []

        def latest():
            asked.append(1)
            return "2026.10.21"
        updates.maybe_auto_update(nightly, self.NIGHT, latest=latest)
        updates.maybe_auto_update(nightly, self.NIGHT + timedelta(minutes=15), latest=latest)
        assert len(asked) == 1
        updates.maybe_auto_update(nightly, self.NIGHT + timedelta(days=1), latest=latest)
        assert len(asked) == 2

    def test_without_github_it_tries_once_a_week(self, nightly):
        from datetime import timedelta
        result = updates.result_path(nightly)
        result.write_text(json.dumps({"status": "ok", "finished_at": "2026-11-25T06:40:00Z"}))
        assert not updates.maybe_auto_update(nightly, self.NIGHT, latest=lambda: None)
        result.write_text(json.dumps({"status": "ok", "finished_at": "2026-11-18T06:40:00Z"}))
        assert updates.maybe_auto_update(nightly, self.NIGHT + timedelta(days=1), latest=lambda: None)
