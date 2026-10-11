"""The Box pages: Linux login, SSH, Wi-Fi, restarting, task progress."""

import pytest

from app.services.system_helper import HelperFailed
from tests.fake_helper import FakeHelper, job


@pytest.fixture
def helper(app):
    fake = FakeHelper()
    app.state.system_helper = fake
    app.state.config.parent.system_setup_done = True
    return fake


def test_needs_a_parent(client, helper):
    assert client.get("/parent/system").status_code in (302, 303)


def test_without_the_helper_the_page_explains(authed_client, app):
    resp = authed_client.get("/parent/system")
    assert resp.status_code == 200 and "only work on the ready-made Raspberry Pi image" in resp.text


def test_overview_shows_network_and_login(authed_client, helper):
    page = authed_client.get("/parent/system").text
    assert "Ethernet cable" in page and "192.0.2.16" in page and "Home Net" in page
    assert 'value="admin" autocomplete="username"' in page
    assert "Turn SSH on" in page


def test_linux_password_change_needs_the_parent_password(authed_client, helper):
    helper.responses["account_set_password"] = {"user": "admin"}
    bad = authed_client.post("/parent/system/linux-password", data={
        "parent_password": "nope", "password": "linuxpass99", "password2": "linuxpass99"})
    assert bad.status_code == 400 and "incorrect" in bad.text
    assert "account_set_password" not in helper.actions()
    ok = authed_client.post("/parent/system/linux-password", data={
        "parent_password": "testpass", "password": "linuxpass99", "password2": "linuxpass99"})
    assert ok.status_code == 200 and "was changed" in ok.text
    assert helper.params("account_set_password") == {"password": "linuxpass99"}


def test_linux_password_cannot_be_the_parent_password(authed_client, helper):
    resp = authed_client.post("/parent/system/linux-password", data={
        "parent_password": "testpass", "password": "testpass", "password2": "testpass"})
    assert resp.status_code == 400 and "parent dashboard password" in resp.text


def test_ssh_toggle(authed_client, helper):
    helper.responses["ssh_set"] = lambda p: {"ssh_enabled": p["enabled"]}
    resp = authed_client.post("/parent/system/ssh", data={"parent_password": "testpass", "enabled": "1"})
    assert "now allowed" in resp.text and helper.params("ssh_set") == {"enabled": True}


def test_ssh_toggle_needs_the_parent_password(authed_client, helper):
    resp = authed_client.post("/parent/system/ssh", data={"parent_password": "x", "enabled": "1"})
    assert resp.status_code == 400 and "ssh_set" not in helper.actions()


def test_scan_lists_networks_and_prefills_the_chosen_one(authed_client, helper):
    helper.responses["wifi_scan"] = {"networks": [{"ssid": "Cafe & Co", "signal": 70, "secured": False}]}
    page = authed_client.get("/parent/system/wifi/scan?ssid=Cafe%20%26%20Co").text
    assert "Cafe &amp; Co" in page and "(open)" in page
    assert 'value="Cafe &amp; Co"' in page


def test_wifi_switch_starts_a_task_and_shows_its_progress(authed_client, helper):
    helper.responses["wifi_set"] = job("abc123", "Switching Wi-Fi")
    resp = authed_client.post("/parent/system/wifi", data={
        "ssid": "Home Net", "password": "long enough", "hidden": "1", "country": "gb"})
    assert resp.status_code == 303
    assert resp.headers["location"] == "/parent/system/jobs/abc123?back=/parent/system"
    assert helper.params("wifi_set") == {"ssid": "Home Net", "password": "long enough",
                                         "hidden": True, "country": "gb"}
    page = authed_client.get(resp.headers["location"]).text
    assert "Working" in page and "step one" in page and 'data-job-poll="/parent/system/jobs/abc123.json"' in page


def test_wifi_refusal_keeps_what_was_typed_except_the_password(authed_client, helper):
    helper.responses["wifi_set"] = HelperFailed("A Wi-Fi name is 1 to 32 characters.")
    resp = authed_client.post("/parent/system/wifi", data={"ssid": "Home Net", "password": "secret-pass"})
    assert resp.status_code == 400 and "1 to 32" in resp.text
    assert 'value="Home Net"' in resp.text and "secret-pass" not in resp.text


def test_finished_and_failed_tasks(authed_client, helper):
    helper.responses["job_status"] = {"id": "j1", "title": "Formatting a drive", "state": "failed",
                                      "messages": ["Erasing"], "error": "The drive may be faulty.",
                                      "result": None}
    page = authed_client.get("/parent/system/jobs/j1").text
    assert "The drive may be faulty." in page and "data-job-poll" not in page


def test_task_json_for_the_progress_poller(authed_client, helper):
    data = authed_client.get("/parent/system/jobs/j9.json").json()
    assert data["id"] == "j9" and data["state"] == "running"


def test_task_page_back_link_stays_on_the_dashboard(authed_client, helper):
    page = authed_client.get("/parent/system/jobs/j1?back=https://evil.example/").text
    assert 'href="/parent/system" class="btn btn-secondary">Back to Login, network &amp; power' in page


@pytest.mark.parametrize("path,action", [
    ("/parent/system/restart", "restart_curatables"),
    ("/parent/system/reboot", "reboot"),
    ("/parent/system/network-restart", "restart_network"),
])
def test_restart_buttons_show_a_page_that_waits_for_the_box(authed_client, helper, path, action):
    helper.responses[action] = {"restarting": True}
    page = authed_client.post(path).text
    assert action in helper.actions()
    assert 'data-wait-healthy="/healthz"' in page


def test_restart_from_diagnostics_returns_there_with_a_note(authed_client, helper):
    helper.responses["restart_curatables"] = {"restarting": True}
    page = authed_client.post("/parent/system/restart", data={"back": "/parent/system/diagnostics"}).text
    assert 'data-wait-back="/parent/system/diagnostics?notice=restarted"' in page
    assert 'data-wait-down="1"' in page


def test_restart_refuses_to_return_off_the_box_pages(authed_client, helper):
    helper.responses["restart_curatables"] = {"restarting": True}
    page = authed_client.post("/parent/system/restart", data={"back": "https://evil.example/"}).text
    assert 'data-wait-back="/parent/system?notice=restarted"' in page


def test_unknown_notices_are_ignored(authed_client, helper):
    page = authed_client.get("/parent/system?notice=<script>").text
    assert "<script>" not in page.split("<body>", 1)[1].split("</nav>", 1)[0]


def test_power_buttons_explain_themselves(authed_client, helper):
    page = authed_client.get("/parent/system").text
    for label in ("Restart Curatables", "Reconnect network", "Restart the box", "Shut down"):
        assert label in page
    assert "Use before unplugging the box" in page


def test_on_the_box_drive_advice_points_to_the_drives_page(authed_client, helper):
    for url in ("/parent/import", "/parent/settings"):
        page = authed_client.get(url).text
        assert "Box → Drives" in page, url
        assert "sudo" not in page and "install.sh" not in page and "docs/deployment.md" not in page, url


def test_the_downloader_result_shows_where_the_notice_says(authed_client, helper, app):
    import json
    from app.services import updates
    updates.result_path(app.state.config).write_text(json.dumps({
        "kind": "yt-dlp", "status": "ok", "message": "Already up to date.",
        "from_version": "2026.08.19", "to_version": "2026.08.19", "finished_at": "2026-10-11 16:20"}))
    for url in ("/parent/system/updates?notice=downloader", "/parent/settings"):
        assert "2026.08.19 was already the newest" in authed_client.get(url).text, url
