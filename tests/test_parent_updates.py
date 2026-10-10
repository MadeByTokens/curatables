"""Box, Updates and Shut down."""

import pytest

from app.services import updates
from app.services.system_helper import HelperFailed
from tests.fake_helper import FakeHelper, job


@pytest.fixture
def helper(app, monkeypatch):
    fake = FakeHelper(update_info={"installed": "0.5.0", "latest": "0.6.0", "available": True,
                                   "notes": "New things", "published": "2026-10-10", "previous": "",
                                   "last": {}, "reboot_required": False})
    app.state.system_helper = fake
    app.state.config.parent.system_setup_done = True
    monkeypatch.setattr(updates, "ytdlp_version", lambda: "2026.09.01")
    return fake


def test_shows_the_available_update(authed_client, helper):
    page = authed_client.get("/parent/system/updates").text
    assert "0.5.0" in page and "Update to 0.6.0" in page and "New things" in page
    assert "Go back to" not in page


def test_up_to_date_shows_no_update_button(authed_client, helper):
    helper.responses["update_info"] = {**helper.responses["update_info"], "available": False, "latest": "0.5.0"}
    page = authed_client.get("/parent/system/updates").text
    assert "newest version" in page and "Update to" not in page


def test_offline_check_says_why(authed_client, helper):
    helper.responses["update_info"] = {"installed": "0.5.0", "check_error": "Could not reach GitHub.",
                                       "previous": "", "last": {}}
    assert "Could not reach GitHub." in authed_client.get("/parent/system/updates").text


def test_previous_version_can_be_restored(authed_client, helper):
    helper.responses["update_info"] = {**helper.responses["update_info"], "previous": "0.4.0",
                                       "last": {"state": "done", "at": "2026-10-10 03:00 UTC",
                                                "message": "Updated from 0.4.0 to 0.5.0."}}
    page = authed_client.get("/parent/system/updates").text
    assert "Go back to 0.4.0" in page and "Updated from 0.4.0 to 0.5.0." in page


@pytest.mark.parametrize("path,action", [
    ("/parent/system/updates/curatables", "curatables_update"),
    ("/parent/system/updates/rollback", "curatables_rollback"),
    ("/parent/system/updates/system", "system_update"),
])
def test_update_buttons_start_tasks(authed_client, helper, path, action):
    helper.responses[action] = job("u1")
    resp = authed_client.post(path)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/parent/system/jobs/u1?back=/parent/system/updates"


def test_refused_update_shows_why(authed_client, helper):
    helper.responses["curatables_update"] = HelperFailed("Curatables 0.5.0 is already the newest version.")
    resp = authed_client.post("/parent/system/updates/curatables")
    assert resp.status_code == 400 and "already the newest" in resp.text


def test_reboot_needed_notice(authed_client, helper):
    helper.responses["update_info"] = {**helper.responses["update_info"], "reboot_required": True}
    page = authed_client.get("/parent/system/updates").text
    assert "need a restart" in page.replace("\n", " ") and 'action="/parent/system/reboot"' in page


def test_task_that_vanished_with_a_helper_restart_returns_to_its_page(authed_client, helper):
    helper.responses["job_status"] = HelperFailed("That task is not known (the helper may have restarted).")
    resp = authed_client.get("/parent/system/jobs/u1?back=/parent/system/updates")
    assert resp.status_code == 303 and resp.headers["location"] == "/parent/system/updates?notice=task-gone"
    assert "helper restarted" in authed_client.get(resp.headers["location"]).text
    assert authed_client.get("/parent/system/jobs/u1.json").json()["state"] == "gone"


def test_shutdown(authed_client, helper):
    helper.responses["shutdown"] = {"shutting_down": True}
    page = authed_client.post("/parent/system/shutdown").text
    assert "shutdown" in helper.actions() and "safe to unplug" in page


def test_shutdown_button_is_on_the_box_page(authed_client, helper):
    assert 'action="/parent/system/shutdown"' in authed_client.get("/parent/system").text
