"""Coverage for app/features/parent_auth/router.py.

Targets the setup, login, and logout edge cases not exercised by
tests/test_routes.py — first-run rendering, validation errors,
redirects when state already exists, kid-profile clearing on
parent login, and explicit logout.
"""


def _clear_password(app) -> None:
    """Drop the conftest fixture's pre-set password so the app is
    back in first-run mode for this test."""
    app.state.config.parent.password_hash = None
    app.state.config.save()


class TestSetupPage:
    def test_renders_form_on_first_run(self, client, app):
        _clear_password(app)
        resp = client.get("/parent/setup")
        assert resp.status_code == 200
        assert b"password" in resp.content.lower()

    def test_redirects_to_login_when_password_already_set(self, client, app):
        # The conftest `app` fixture pre-sets a password.
        resp = client.get("/parent/setup")
        assert resp.status_code == 302
        assert "/parent/login" in resp.headers["location"]


class TestSetupWindow:
    def _expire(self, app):
        import time
        window = app.state.config.parent.setup_window_minutes
        app.state.start_time = time.monotonic() - window * 60 - 1

    def test_setup_closes_after_the_window(self, client, app):
        _clear_password(app)
        self._expire(app)
        resp = client.get("/parent/setup")
        assert resp.status_code == 403
        assert b"Setup has timed out" in resp.content
        resp = client.post("/parent/setup", data={
            "password": "long-enough-pw", "password2": "long-enough-pw"})
        assert resp.status_code == 403
        assert app.state.config.is_first_run

    def test_window_zero_keeps_setup_open(self, client, app):
        _clear_password(app)
        app.state.config.parent.setup_window_minutes = 0
        self._expire(app)
        assert client.get("/parent/setup").status_code == 200

    def test_closed_window_does_not_affect_an_installed_server(self, client, app):
        self._expire(app)
        assert client.get("/parent/setup").status_code == 302


class TestSetupSubmit:
    def test_redirects_to_login_when_password_already_set(self, client, app):
        resp = client.post(
            "/parent/setup",
            data={"password": "newpw", "password2": "newpw"},
        )
        assert resp.status_code == 302
        assert "/parent/login" in resp.headers["location"]

    def test_rejects_short_password(self, client, app):
        _clear_password(app)
        resp = client.post(
            "/parent/setup",
            data={"password": "ab", "password2": "ab"},
        )
        assert resp.status_code == 200
        assert b"at least 8 characters" in resp.content

    def test_rejects_mismatched_passwords(self, client, app):
        _clear_password(app)
        resp = client.post(
            "/parent/setup",
            data={"password": "abcd1234", "password2": "wxyz1234"},
        )
        assert resp.status_code == 200
        assert b"do not match" in resp.content

    def test_success_authenticates_parent(self, client, app):
        _clear_password(app)
        resp = client.post(
            "/parent/setup",
            data={"password": "abcd1234", "password2": "abcd1234"},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/"

        # The session is a parent session: a follow-up parent request
        # does not bounce.
        resp2 = client.get("/parent/")
        assert resp2.status_code == 200

    def test_clears_kid_profile_from_session(self, client, app):
        """Completing setup while a kid profile is selected in the
        session drops the profile so get_viewer resolves as parent."""
        from app.dependencies import get_db
        from app.repositories import ProfileRepository
        from app.models import Profile

        conn = next(app.dependency_overrides[get_db]())
        pid = ProfileRepository(conn).create(
            Profile(name="alice", display_name="Alice", pin="",
                    allowed_channel_ids=[])
        )
        client.post("/profiles/select", data={"profile_id": str(pid)})

        _clear_password(app)
        resp = client.post(
            "/parent/setup",
            data={"password": "abcd1234", "password2": "abcd1234"},
        )
        assert resp.status_code == 302

        # The parent landing page resolves as parent, not kid.
        resp2 = client.get("/parent/")
        assert resp2.status_code == 200


class TestLoginPage:
    def test_redirects_to_setup_on_first_run(self, client, app):
        _clear_password(app)
        resp = client.get("/parent/login")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/setup"

    def test_redirects_authed_parent_to_dashboard(self, authed_client):
        resp = authed_client.get("/parent/login")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/"


class TestLogout:
    def test_get_does_not_log_out(self, authed_client):
        resp = authed_client.get("/parent/logout")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/"
        assert authed_client.get("/parent/").status_code == 200

    def test_clears_session_and_redirects_to_login(self, authed_client):
        page = authed_client.get("/parent/settings").text
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
        resp = authed_client.post("/parent/logout", data={"csrf_token": token})
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/login"

        # And the session really is gone — the next /parent/ request
        # bounces back to /parent/login.
        resp2 = authed_client.get("/parent/")
        assert resp2.status_code == 302
        assert "/parent/login" in resp2.headers["location"]


class TestPasswordManagerMarkup:
    """Every password form names its account and says which password
    it wants, so password managers can save and suggest the right one."""

    def _form(self, html, action):
        start = html.index(f'action="{action}"')
        return html[start:html.index("</form>", start)]

    def _check(self, form, *autocompletes):
        assert 'name="username" value="parent" autocomplete="username"' in form
        for value in autocompletes:
            assert f'autocomplete="{value}"' in form
        assert "onpaste" not in form and "maxlength" not in form

    def test_setup_form(self, client, app):
        _clear_password(app)
        html = client.get("/parent/setup").text
        self._check(self._form(html, "/parent/setup"), "new-password")

    def test_login_form(self, client):
        html = client.get("/parent/login").text
        self._check(self._form(html, "/parent/login"), "current-password")

    def test_change_password_form(self, authed_client):
        html = authed_client.get("/parent/settings").text
        self._check(self._form(html, "/parent/settings/password"),
                    "current-password", "new-password")
