"""Coverage for app/features/parent_profiles/router.py.

Targets the create / edit / delete flows: form rendering, the
empty-name validation branch, channel-id wiring, PIN validation,
the missing-profile redirect on edit, and delete.
"""

from app.services.profiles import ProfileService


def _create_profile(app, name="Frank", **kwargs) -> int:
    from app.dependencies import get_db
    from app.repositories import ProfileRepository
    from app.models import Profile

    conn = next(app.dependency_overrides[get_db]())
    return ProfileRepository(conn).create(
        Profile(name=name.lower(), display_name=name, pin="",
                allowed_channel_ids=[], **kwargs)
    )


class TestProfilesList:
    def test_renders_list_for_parent(self, authed_client):
        resp = authed_client.get("/parent/profiles/")
        assert resp.status_code == 200

    def test_unauthenticated_redirects_to_login(self, client, app):
        resp = client.get("/parent/profiles/")
        assert resp.status_code == 302
        assert "/parent/login" in resp.headers["location"]


class TestProfilesCreateForm:
    def test_renders_for_parent(self, authed_client):
        resp = authed_client.get("/parent/profiles/create")
        assert resp.status_code == 200


class TestProfilesCreateSubmit:
    def test_empty_display_name_shows_error(self, authed_client):
        resp = authed_client.post(
            "/parent/profiles/create",
            data={"display_name": "   "},
        )
        assert resp.status_code == 400
        assert b"Name is required" in resp.content

    def test_happy_path_persists_profile(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        resp = authed_client.post(
            "/parent/profiles/create",
            data={
                "display_name": "Charlie",
                "pin": "1234",
                "avatar": "default",
                "theme": "base",
                "search_mode": "disabled",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/profiles"

        conn = next(app.dependency_overrides[get_db]())
        profiles = ProfileRepository(conn).list()
        charlie = next(p for p in profiles if p.display_name == "Charlie")
        assert charlie.pin.startswith("pbkdf2$")
        assert ProfileService(ProfileRepository(conn)).check_pin(charlie, "1234")
        assert charlie.avatar == "default"

    def test_channel_ids_are_persisted(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ChannelRepository, ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        ch_repo = ChannelRepository(conn)
        ch1 = ch_repo.create("Animals")
        ch2 = ch_repo.create("Music")

        resp = authed_client.post(
            "/parent/profiles/create",
            data={
                "display_name": "Dana",
                "pin": "",
                "avatar": "bear",
                "theme": "playful",
                "search_mode": "curated",
                "channel_ids": [str(ch1), str(ch2)],
            },
        )
        assert resp.status_code == 302

        dana = next(p for p in ProfileRepository(conn).list()
                    if p.display_name == "Dana")
        assert sorted(dana.allowed_channel_ids or []) == sorted([ch1, ch2])

    def test_non_digit_channel_ids_are_dropped(self, authed_client, app):
        """The router filters channel_ids by `x.isdigit()` so a stray
        non-numeric token in the form does not blow up `int(...)`."""
        from app.dependencies import get_db
        from app.repositories import ChannelRepository, ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        ch_id = ChannelRepository(conn).create("Animals")

        resp = authed_client.post(
            "/parent/profiles/create",
            data={
                "display_name": "Drew",
                "pin": "",
                "avatar": "default",
                "theme": "base",
                "search_mode": "disabled",
                "channel_ids": [str(ch_id), "not-a-number", ""],
            },
        )
        assert resp.status_code == 302

        drew = next(p for p in ProfileRepository(conn).list()
                    if p.display_name == "Drew")
        assert drew.allowed_channel_ids == [ch_id]

    def test_non_digit_or_long_pin_is_rejected(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        resp = authed_client.post(
            "/parent/profiles/create",
            data={"display_name": "Eve", "pin": "1" * 50},
        )
        assert resp.status_code == 400
        assert b"4 to 6 digits" in resp.content
        conn = next(app.dependency_overrides[get_db]())
        assert not any(p.display_name == "Eve"
                       for p in ProfileRepository(conn).list())


class TestProfilesEditForm:
    def test_renders_for_existing_profile(self, authed_client, app):
        pid = _create_profile(app, name="Gary")
        resp = authed_client.get(f"/parent/profiles/{pid}/edit")
        assert resp.status_code == 200

    def test_redirects_for_missing_profile(self, authed_client):
        resp = authed_client.get("/parent/profiles/9999/edit")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/profiles"


class TestProfilesEditSubmit:
    def test_updates_fields(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        pid = _create_profile(app, name="Hugo")
        resp = authed_client.post(
            f"/parent/profiles/{pid}/edit",
            data={
                "display_name": "Hugo the Great",
                "pin": "9999",
                "avatar": "fox",
                "theme": "calm",
                "search_mode": "curated",
            },
        )
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/profiles"

        conn = next(app.dependency_overrides[get_db]())
        prof = ProfileRepository(conn).get(pid)
        assert prof.display_name == "Hugo the Great"
        assert ProfileService(ProfileRepository(conn)).check_pin(prof, "9999")
        assert prof.theme == "calm"
        assert prof.search_mode == "curated"

    def test_unticking_every_channel_removes_restriction(
            self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ChannelRepository, ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        ch_id = ChannelRepository(conn).create("Animals")
        pid = _create_profile(app, name="Iris")
        ProfileRepository(conn).update(pid, allowed_channel_ids=[ch_id])
        assert ProfileRepository(conn).get(pid).channels_restricted

        resp = authed_client.post(
            f"/parent/profiles/{pid}/edit",
            data={
                "display_name": "Iris",
                "pin": "",
                "avatar": "default",
                "theme": "base",
                "search_mode": "disabled",
            },
        )
        assert resp.status_code == 302

        prof = ProfileRepository(conn).get(pid)
        assert prof.allowed_channel_ids == []
        assert not prof.channels_restricted

    def test_none_of_yours_hides_every_parent_channel(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        pid = _create_profile(app, name="Jo")
        authed_client.post(
            f"/parent/profiles/{pid}/edit",
            data={"display_name": "Jo", "channel_access": "none"},
        )
        prof = ProfileRepository(conn).get(pid)
        assert prof.channels_restricted
        assert prof.allowed_channel_ids == []

    def test_unknown_choices_fall_back_to_defaults(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        pid = _create_profile(app, name="Kim")
        authed_client.post(
            f"/parent/profiles/{pid}/edit",
            data={"display_name": "Kim", "theme": "../../etc",
                  "search_mode": "open", "avatar": "<b>"},
        )
        prof = ProfileRepository(conn).get(pid)
        assert prof.theme == "base"
        assert prof.search_mode == "disabled"
        assert prof.avatar == "default"


class TestPinHandling:
    def test_blank_pin_keeps_existing_pin(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        svc = ProfileService(ProfileRepository(conn))
        pid = svc.create(name="pia", display_name="Pia", pin="2468")
        authed_client.post(f"/parent/profiles/{pid}/edit",
                           data={"display_name": "Pia", "pin": ""})
        assert svc.check_pin(svc.get(pid), "2468")

    def test_remove_pin_box_clears_pin(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        svc = ProfileService(ProfileRepository(conn))
        pid = svc.create(name="quin", display_name="Quin", pin="2468")
        authed_client.post(f"/parent/profiles/{pid}/edit",
                           data={"display_name": "Quin", "remove_pin": "1"})
        assert svc.get(pid).pin == ""

    def test_edit_form_never_echoes_pin(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        pid = ProfileService(ProfileRepository(conn)).create(
            name="ron", display_name="Ron", pin="1357")
        page = authed_client.get(f"/parent/profiles/{pid}/edit").text
        assert "1357" not in page
        assert "pbkdf2" not in page


class TestProfilesDelete:
    def test_removes_profile(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        pid = _create_profile(app, name="Jess")
        conn = next(app.dependency_overrides[get_db]())
        assert ProfileRepository(conn).get(pid) is not None

        resp = authed_client.post(f"/parent/profiles/{pid}/delete")
        assert resp.status_code == 302
        assert resp.headers["location"] == "/parent/profiles"

        assert ProfileRepository(conn).get(pid) is None
