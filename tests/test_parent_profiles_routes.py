"""Coverage for app/features/parent_profiles/router.py.

Targets the create / edit / delete flows: form rendering, the
empty-name validation branch, channel-id wiring, PIN truncation,
the missing-profile redirect on edit, and delete.
"""


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
        assert resp.status_code == 200
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
        assert charlie.pin == "1234"
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

    def test_pin_is_truncated_to_ten_chars(self, authed_client, app):
        from app.dependencies import get_db
        from app.repositories import ProfileRepository

        resp = authed_client.post(
            "/parent/profiles/create",
            data={
                "display_name": "Eve",
                "pin": "1" * 50,
                "avatar": "default",
                "theme": "base",
                "search_mode": "disabled",
            },
        )
        assert resp.status_code == 302

        conn = next(app.dependency_overrides[get_db]())
        eve = next(p for p in ProfileRepository(conn).list()
                   if p.display_name == "Eve")
        assert len(eve.pin) <= 10


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
        assert prof.pin == "9999"
        assert prof.theme == "calm"
        assert prof.search_mode == "curated"

    def test_existing_channel_restriction_is_preserved_when_form_omits_ids(
            self, authed_client, app):
        """The router computes `channel_ids or None` and the repo
        treats `None` as 'do not touch profile_channels'. So an edit
        form that submits nothing under the `channel_ids` key keeps
        the existing restriction intact rather than wiping it.
        """
        from app.dependencies import get_db
        from app.repositories import ChannelRepository, ProfileRepository

        conn = next(app.dependency_overrides[get_db]())
        ch_id = ChannelRepository(conn).create("Animals")
        pid = _create_profile(app, name="Iris")
        ProfileRepository(conn).update(pid, allowed_channel_ids=[ch_id])

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
        assert prof.allowed_channel_ids == [ch_id]


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
