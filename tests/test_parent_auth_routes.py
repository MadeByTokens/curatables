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
        assert b"at least 4 characters" in resp.content

    def test_rejects_mismatched_passwords(self, client, app):
        _clear_password(app)
        resp = client.post(
            "/parent/setup",
            data={"password": "abcd", "password2": "wxyz"},
        )
        assert resp.status_code == 200
        assert b"do not match" in resp.content

    def test_success_authenticates_parent(self, client, app):
        _clear_password(app)
        resp = client.post(
            "/parent/setup",
            data={"password": "abcd", "password2": "abcd"},
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/"

        # The new credentials are persisted and the session is now a
        # parent session — a follow-up parent request should not bounce.
        resp2 = client.get("/parent/")
        assert resp2.status_code == 200

    def test_clears_kid_profile_from_session(self, client, app):
        """Mirror of the login-side regression test: completing setup
        while a kid profile is selected in the session must drop the
        profile so get_viewer resolves as parent."""
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
            data={"password": "abcd", "password2": "abcd"},
        )
        assert resp.status_code == 302

        # The parent landing page resolves successfully (we are a
        # parent now, not a kid).
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
    def test_clears_session_and_redirects_to_login(self, authed_client):
        resp = authed_client.get("/parent/logout")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/login"

        # And the session really is gone — the next /parent/ request
        # bounces back to /parent/login.
        resp2 = authed_client.get("/parent/")
        assert resp2.status_code == 302
        assert "/parent/login" in resp2.headers["location"]
