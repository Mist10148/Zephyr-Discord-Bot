"""Tests for validate_web_config().

This runs at import time in wsgi.py and run_web.py, before the app exists, so a
wrong answer here takes the whole website down rather than degrading one endpoint.

zephyr/config.py binds its values at import, so each case reloads the module with a
patched environment instead of setting attributes after the fact.
"""

import importlib

import pytest


def _reload_config(monkeypatch, **env):
    """Re-import zephyr.config with a controlled environment."""
    import zephyr.config as config

    for name in (
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "REDIS_URL",
        "REDISCLOUD_URL",
        "WEB_PUBLIC_URL",
        "RENDER_EXTERNAL_URL",
        "DISCORD_REDIRECT_URI",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    # load_dotenv does not override variables that are already set, but the repo's
    # real .env could still supply one we deleted, so neutralise it.
    monkeypatch.setattr("zephyr.config.load_dotenv", lambda *a, **k: None, raising=False)
    return importlib.reload(config)


class TestUnconfigured:
    def test_nothing_set_is_fine(self, monkeypatch):
        """The public weather deployment must keep booting."""
        config = _reload_config(monkeypatch)
        assert config.AUTH_ENABLED is False
        assert config.validate_web_config() is None

    def test_redis_alone_is_fine(self, monkeypatch):
        """REDIS_URL predates the dashboard and still has its own job.

        This is the case that would otherwise break a deploy: render.yaml wires
        REDIS_URL into the web service while the OAuth secrets start blank.
        """
        config = _reload_config(monkeypatch, REDIS_URL="redis://localhost:6379/0")
        assert config.AUTH_ENABLED is False
        assert config.validate_web_config() is None


class TestPartialConfiguration:
    def test_a_client_id_without_a_secret_raises(self, monkeypatch):
        config = _reload_config(monkeypatch, DISCORD_CLIENT_ID="cid")
        with pytest.raises(RuntimeError, match="DISCORD_CLIENT_SECRET"):
            config.validate_web_config()

    def test_a_secret_without_a_client_id_raises(self, monkeypatch):
        config = _reload_config(monkeypatch, DISCORD_CLIENT_SECRET="secret")
        with pytest.raises(RuntimeError, match="DISCORD_CLIENT_ID"):
            config.validate_web_config()

    def test_credentials_without_redis_raise(self, monkeypatch):
        config = _reload_config(monkeypatch, DISCORD_CLIENT_ID="cid", DISCORD_CLIENT_SECRET="secret")
        with pytest.raises(RuntimeError, match="REDIS_URL"):
            config.validate_web_config()


class TestFullyConfigured:
    def test_all_three_enables_auth(self, monkeypatch):
        config = _reload_config(
            monkeypatch,
            DISCORD_CLIENT_ID="cid",
            DISCORD_CLIENT_SECRET="secret",
            REDIS_URL="redis://localhost:6379/0",
        )
        assert config.AUTH_ENABLED is True
        assert config.validate_web_config() is None

    def test_the_redirect_uri_is_derived_from_the_public_origin(self, monkeypatch):
        config = _reload_config(
            monkeypatch,
            DISCORD_CLIENT_ID="cid",
            DISCORD_CLIENT_SECRET="secret",
            REDIS_URL="redis://localhost:6379/0",
            WEB_PUBLIC_URL="https://zephyr.example.com/",
        )
        assert config.DISCORD_REDIRECT_URI == "https://zephyr.example.com/api/v1/auth/callback"
        # https origins get a Secure cookie without anybody remembering to ask.
        assert config.AUTH_COOKIE_SECURE is True

    def test_render_supplies_the_origin(self, monkeypatch):
        config = _reload_config(
            monkeypatch,
            DISCORD_CLIENT_ID="cid",
            DISCORD_CLIENT_SECRET="secret",
            REDIS_URL="redis://localhost:6379/0",
            RENDER_EXTERNAL_URL="https://zephyr.onrender.com",
        )
        assert config.DISCORD_REDIRECT_URI == "https://zephyr.onrender.com/api/v1/auth/callback"

    def test_web_app_url_is_never_used_as_the_oauth_origin(self, monkeypatch):
        """Its default is a hardcoded ngrok host Discord would reject."""
        config = _reload_config(
            monkeypatch,
            DISCORD_CLIENT_ID="cid",
            DISCORD_CLIENT_SECRET="secret",
            REDIS_URL="redis://localhost:6379/0",
        )
        assert "ngrok" not in config.DISCORD_REDIRECT_URI
        assert config.DISCORD_REDIRECT_URI.startswith("http://127.0.0.1")

    def test_a_local_origin_leaves_the_cookie_insecure(self, monkeypatch):
        config = _reload_config(
            monkeypatch,
            DISCORD_CLIENT_ID="cid",
            DISCORD_CLIENT_SECRET="secret",
            REDIS_URL="redis://localhost:6379/0",
            WEB_PUBLIC_URL="http://127.0.0.1:5000",
        )
        assert config.AUTH_COOKIE_SECURE is False


class TestRedirectUriShape:
    def test_a_relative_redirect_uri_raises(self, monkeypatch):
        config = _reload_config(
            monkeypatch,
            DISCORD_CLIENT_ID="cid",
            DISCORD_CLIENT_SECRET="secret",
            REDIS_URL="redis://localhost:6379/0",
            DISCORD_REDIRECT_URI="/api/v1/auth/callback",
        )
        with pytest.raises(RuntimeError, match="absolute"):
            config.validate_web_config()

    def test_a_redirect_uri_with_the_wrong_path_raises(self, monkeypatch):
        config = _reload_config(
            monkeypatch,
            DISCORD_CLIENT_ID="cid",
            DISCORD_CLIENT_SECRET="secret",
            REDIS_URL="redis://localhost:6379/0",
            DISCORD_REDIRECT_URI="https://zephyr.example.com/callback",
        )
        with pytest.raises(RuntimeError, match="/api/v1/auth/callback"):
            config.validate_web_config()


@pytest.fixture(autouse=True)
def restore_config():
    """Leave zephyr.config as the rest of the suite expects to find it."""
    yield
    import zephyr.config

    importlib.reload(zephyr.config)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
