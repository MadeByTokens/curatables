"""Service layer tests — business logic with real in-memory SQLite."""

import pytest

from app.models import Video, Profile
from app.repositories import ChannelRepository, ProfileRepository
from app.services.channels import ChannelService
from app.services.profiles import ProfileService, slugify


class TestChannelService:
    def test_list_with_counts_delegates_to_repo(self, db):
        repo = ChannelRepository(db)
        service = ChannelService(repo)
        repo.create("Animals")
        result = service.list_with_counts()
        assert len(result) == 1
        assert result[0][0].name == "Animals"
        assert result[0][1] == 0


class TestProfileService:
    def test_create_and_list(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)

        service.create(
            name="kid1", display_name="Kiddo",
            pin="", avatar="bear", theme="base",
            search_mode="disabled", allowed_channel_ids=[],
        )
        profiles = service.list()
        assert len(profiles) == 1
        assert profiles[0].name == "kid1"
        assert profiles[0].avatar == "bear"

    def test_get_returns_profile(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)
        pid = service.create(
            name="kid2", display_name="Kid Two", pin="",
            avatar="default", theme="base",
            search_mode="disabled", allowed_channel_ids=[],
        )
        prof = service.get(pid)
        assert prof is not None
        assert prof.display_name == "Kid Two"

    def test_get_returns_none_for_missing_id(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)
        assert service.get(999999) is None

    def test_update_passes_fields_through_to_repo(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)
        pid = service.create(
            name="kid3", display_name="Original", pin="",
            avatar="default", theme="base",
            search_mode="disabled", allowed_channel_ids=[],
        )
        service.update(pid, display_name="Renamed", pin="0000",
                       avatar="bear", theme="playful",
                       search_mode="curated")
        prof = service.get(pid)
        assert prof.display_name == "Renamed"
        assert prof.pin == "0000"
        assert prof.theme == "playful"
        assert prof.search_mode == "curated"

    def test_delete_removes_profile(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)
        pid = service.create(
            name="kid4", display_name="Going", pin="",
            avatar="default", theme="base",
            search_mode="disabled", allowed_channel_ids=[],
        )
        assert service.get(pid) is not None
        service.delete(pid)
        assert service.get(pid) is None


class TestSlugify:
    def test_basic_lowercase_and_hyphenation(self):
        assert slugify("Hello World") == "hello-world"

    def test_strips_unicode_accents(self):
        # NFKD-normalised "Renée" loses the acute via the ascii-encode
        # round-trip.
        assert slugify("Renée") == "renee"

    def test_collapses_runs_of_punctuation(self):
        assert slugify("Foo!! @ Bar??") == "foo-bar"

    def test_strips_leading_and_trailing_separators(self):
        assert slugify("  --hello--  ") == "hello"

    def test_empty_string_falls_back_to_kid(self):
        assert slugify("") == "kid"

    def test_only_punctuation_falls_back_to_kid(self):
        assert slugify("***") == "kid"

    def test_none_is_treated_as_empty(self):
        # The function uses `text or ""` to tolerate None.
        assert slugify(None) == "kid"


class TestProfileServiceUniqueSlug:
    def test_returns_base_when_no_collision(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)
        assert service.unique_slug("Alice") == "alice"

    def test_appends_suffix_on_first_collision(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)
        repo.create(Profile(name="alice", display_name="Alice", pin=""))
        assert service.unique_slug("Alice") == "alice-2"

    def test_increments_until_free(self, db):
        repo = ProfileRepository(db)
        service = ProfileService(repo)
        for n in ("alice", "alice-2", "alice-3"):
            repo.create(Profile(name=n, display_name=n, pin=""))
        assert service.unique_slug("Alice") == "alice-4"
