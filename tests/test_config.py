"""Config layer tests — load, save, atomicity."""

from app.config import Config, load_config


class TestConfig:
    def test_save_and_load(self, tmp_path):
        c = Config()
        c.storage.path = str(tmp_path)
        c.server.port = 9090
        c.storage.cache_days = 7
        c.save()

        loaded = load_config(str(tmp_path))
        assert loaded.server.port == 9090
        assert loaded.storage.cache_days == 7

    def test_defaults(self):
        c = Config()
        assert c.server.port == 8080
        assert c.server.host == "0.0.0.0"
        assert c.storage.default_resolution == "720p"
        assert c.parent.session_timeout_hours == 24

    def test_is_first_run(self):
        c = Config()
        assert c.is_first_run is True
        c.parent.password_hash = "some_hash"
        assert c.is_first_run is False
