"""First-run step 2 (the Linux login password) and keeping the two
passwords different, with the system helper faked."""

import pytest
from fastapi.testclient import TestClient

from app.services.system_helper import HelperFailed, HelperUnavailable


class FakeHelper:
    def __init__(self, *, has_password=False, linux_password=None, ssh=False, broken=False):
        self.available = True
        self.calls = []
        self.has_password = has_password
        self.linux_password = linux_password
        self.ssh = ssh
        self.broken = broken

    def call(self, action, **params):
        self.calls.append((action, params))
        if self.broken:
            raise HelperUnavailable("The system helper is not answering.")
        if action == "account_status":
            return {"user": "admin", "has_password": self.has_password, "ssh_enabled": self.ssh}
        if action == "account_password_matches":
            return {"matches": params["password"] == self.linux_password}
        if action == "account_set_password":
            if len(params["password"]) < 8:
                raise HelperFailed("too short")
            self.linux_password = params["password"]
            self.has_password = True
            return {"user": "admin"}
        if action == "ssh_set":
            self.ssh = params["enabled"]
            return {"ssh_enabled": self.ssh}
        raise AssertionError(action)

    def linux_password_matches(self, password):
        return self.call("account_password_matches", password=password)["matches"]

    def actions(self):
        return [a for a, _ in self.calls]


@pytest.fixture
def helper(app):
    fake = FakeHelper()
    app.state.system_helper = fake
    return fake


def _first_run(app):
    app.state.config.parent.password_hash = None
    app.state.config.save()


def test_without_a_helper_setup_is_one_step(client, app):
    _first_run(app)
    resp = client.post("/parent/setup", data={"password": "parentpass1", "password2": "parentpass1"})
    assert resp.headers["location"] == "/parent/"
    assert client.get("/parent/").status_code == 200


def test_step_one_leads_to_step_two_on_the_box(client, app, helper):
    _first_run(app)
    resp = client.post("/parent/setup", data={"password": "parentpass1", "password2": "parentpass1"})
    assert resp.headers["location"] == "/parent/setup/system"
    page = client.get("/parent/setup/system")
    assert page.status_code == 200
    assert 'value="admin" autocomplete="username"' in page.text
    assert 'name="ssh" value="1">' in page.text


def test_dashboard_stays_closed_until_step_two_is_done(authed_client, helper):
    for path in ("/parent/", "/parent/settings", "/parent/content"):
        resp = authed_client.get(path)
        assert resp.status_code == 302 and resp.headers["location"] == "/parent/setup/system"
    kid = authed_client.get("/profiles")
    assert kid.headers.get("location") != "/parent/setup/system"


def test_step_two_sets_the_linux_password_and_leaves_ssh_off(authed_client, app, helper):
    resp = authed_client.post("/parent/setup/system", data={
        "mode": "new", "password": "linuxpass99", "password2": "linuxpass99"})
    assert resp.status_code == 302 and resp.headers["location"] == "/parent/"
    assert ("account_set_password", {"password": "linuxpass99"}) in helper.calls
    assert "ssh_set" not in helper.actions()
    assert app.state.config.parent.system_setup_done
    assert authed_client.get("/parent/").status_code == 200


def test_step_two_can_turn_ssh_on(authed_client, helper):
    authed_client.post("/parent/setup/system", data={
        "mode": "new", "password": "linuxpass99", "password2": "linuxpass99", "ssh": "1"})
    assert ("ssh_set", {"enabled": True}) in helper.calls


def test_step_two_refuses_the_parent_password(authed_client, app, helper):
    resp = authed_client.post("/parent/setup/system", data={
        "mode": "new", "password": "testpass", "password2": "testpass"})
    assert resp.status_code == 400 and "parent dashboard password" in resp.text
    assert "account_set_password" not in helper.actions()
    assert not app.state.config.parent.system_setup_done


@pytest.mark.parametrize("data,message", [
    ({"password": "short", "password2": "short"}, "at least"),
    ({"password": "linuxpass99", "password2": "linuxpass98"}, "do not match"),
])
def test_step_two_checks_the_new_password(authed_client, helper, data, message):
    resp = authed_client.post("/parent/setup/system", data={"mode": "new", **data})
    assert resp.status_code == 400 and message in resp.text


def test_keeping_a_password_set_by_imager_needs_it_typed(authed_client, app, helper):
    helper.has_password = True
    helper.linux_password = "imagerpass1"
    page = authed_client.get("/parent/setup/system")
    assert 'value="keep"' in page.text and 'autocomplete="current-password"' in page.text
    bad = authed_client.post("/parent/setup/system", data={"mode": "keep", "password": "wrongpass1"})
    assert bad.status_code == 400 and "not the current password" in bad.text
    ok = authed_client.post("/parent/setup/system", data={"mode": "keep", "password": "imagerpass1"})
    assert ok.status_code == 302
    assert "account_set_password" not in helper.actions()
    assert app.state.config.parent.system_setup_done


def test_keep_is_not_offered_when_no_password_was_set(authed_client, helper):
    assert 'value="keep"' not in authed_client.get("/parent/setup/system").text
    resp = authed_client.post("/parent/setup/system", data={"mode": "keep", "password": "whatever1"})
    assert resp.status_code == 400


def test_step_one_refuses_the_linux_password(client, app, helper):
    _first_run(app)
    helper.linux_password = "imagerpass1"
    resp = client.post("/parent/setup", data={"password": "imagerpass1", "password2": "imagerpass1"})
    assert resp.status_code == 200 and "Linux login password" in resp.text
    assert app.state.config.is_first_run


def test_changing_the_parent_password_to_the_linux_one_is_refused(authed_client, app, helper):
    app.state.config.parent.system_setup_done = True
    helper.linux_password = "linuxpass99"
    resp = authed_client.post("/parent/settings/password", data={
        "current_password": "testpass", "new_password": "linuxpass99", "new_password2": "linuxpass99"})
    assert "Linux login password" in resp.text
    from app.services.auth import AuthService
    assert AuthService(app.state.config).verify_password("testpass")


def test_a_broken_helper_offers_to_skip(authed_client, app, helper):
    helper.broken = True
    page = authed_client.get("/parent/setup/system")
    assert page.status_code == 503 and "Skip for now" in page.text
    resp = authed_client.post("/parent/setup/system", data={"mode": "later"})
    assert resp.status_code == 302 and app.state.config.parent.system_setup_done


def test_skip_is_refused_while_the_helper_works(authed_client, app, helper):
    resp = authed_client.post("/parent/setup/system", data={"mode": "later", "password": ""})
    assert resp.status_code == 400
    assert not app.state.config.parent.system_setup_done


def test_step_two_needs_a_parent_session(client, app, helper):
    resp = client.get("/parent/setup/system")
    assert resp.status_code in (302, 303) and "/parent/login" in resp.headers["location"]


def test_login_page_stays_reachable_before_step_two(app, helper):
    c = TestClient(app, follow_redirects=False)
    assert c.get("/parent/login").status_code == 200


def test_a_failing_ssh_switch_does_not_trap_the_parent_in_setup(authed_client, app, helper):
    helper.ssh = True
    real = helper.call

    def call(action, **params):
        if action == "ssh_set":
            raise HelperFailed("SSH could not be turned off: no systemd")
        return real(action, **params)
    helper.call = call
    resp = authed_client.post("/parent/setup/system", data={
        "mode": "new", "password": "linuxpass99", "password2": "linuxpass99"})
    assert resp.status_code == 302 and resp.headers["location"] == "/parent/system?notice=ssh-failed#ssh"
    assert app.state.config.parent.system_setup_done
    assert helper.linux_password == "linuxpass99"
