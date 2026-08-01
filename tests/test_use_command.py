"""Tests for the /use link.

The link this command hands out is the only route most people will ever take into
the dashboard, and it is easy to break in a way nothing else notices: point it at
the wrong host and every sign-in dies at the OAuth callback instead of at the click,
which looks like a Discord problem rather than a config one.

zephyr.config binds its values at import, and zephyr.cogs.weather imports them by
name at *its* import, so both modules are reloaded together with a patched
environment rather than having attributes set after the fact.
"""

import importlib
from urllib.parse import parse_qs, urlparse

import pytest


def _reload_cog(monkeypatch, **env):
    """Re-import zephyr.config and the weather cog with a controlled environment."""
    import zephyr.config as config

    for name in (
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "REDIS_URL",
        "REDISCLOUD_URL",
        "WEB_PUBLIC_URL",
        "RENDER_EXTERNAL_URL",
        "DISCORD_REDIRECT_URI",
        "WEB_APP_URL",
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    # Same reasoning as tests/test_web_config.py: neutralise the repo's real .env on
    # the dotenv module, because reload rebinds the imported name.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    importlib.reload(config)
    import zephyr.cogs.weather as weather

    return importlib.reload(weather)


DASHBOARD_ENV = {
    "DISCORD_CLIENT_ID": "cid",
    "DISCORD_CLIENT_SECRET": "secret",
    "REDIS_URL": "redis://localhost:6379/0",
    "WEB_PUBLIC_URL": "https://zephyr.example.com",
}


class TestDashboardConfigured:
    def test_link_goes_through_sign_in_and_lands_on_the_invoking_guild(self, monkeypatch):
        weather = _reload_cog(monkeypatch, **DASHBOARD_ENV)
        url = urlparse(weather._web_app_link(1234567890))

        assert url.scheme == "https"
        assert url.netloc == "zephyr.example.com"
        # The sign-in endpoint, not the site root: arriving signed-out and hunting
        # for a login is the failure this command exists to avoid.
        assert url.path == "/api/v1/auth/login"
        assert parse_qs(url.query)["next"] == ["/g/1234567890"]

    def test_a_dm_falls_back_to_the_server_picker(self, monkeypatch):
        """guild_id is None outside a guild; /g/None would be a 400 from the API."""
        weather = _reload_cog(monkeypatch, **DASHBOARD_ENV)
        url = urlparse(weather._web_app_link(None))
        assert parse_qs(url.query)["next"] == ["/g"]

    def test_the_oauth_base_is_never_web_app_url(self, monkeypatch):
        """The mirror of test_web_config's rule, enforced at the link's other end.

        OAuth returns to the redirect URI registered with Discord, which is derived
        from WEB_PUBLIC_URL. Building the sign-in link on WEB_APP_URL instead would
        fail at the callback, long after the user has left Discord.
        """
        weather = _reload_cog(
            monkeypatch, **DASHBOARD_ENV, WEB_APP_URL="https://somewhere-else.example.com/"
        )
        link = weather._web_app_link(42)
        assert "somewhere-else" not in link
        assert link.startswith("https://zephyr.example.com/")

    def test_a_trailing_slash_does_not_double_up(self, monkeypatch):
        weather = _reload_cog(
            monkeypatch, **{**DASHBOARD_ENV, "WEB_PUBLIC_URL": "https://zephyr.example.com/"}
        )
        assert "//api/v1" not in weather._web_app_link(42)

    def test_embed_offers_a_working_link_button(self, monkeypatch):
        weather = _reload_cog(monkeypatch, **DASHBOARD_ENV)
        embed, view = weather._web_app_embed(42)

        assert "Sign in" in embed.description or "Sign in" in embed.title
        assert view is not None
        button = view.children[0]
        assert button.url == weather._web_app_link(42)
        # A link button carries no custom_id, which is what makes timeout=None safe.
        assert button.custom_id is None


class TestNoDashboard:
    def test_falls_back_to_the_plain_public_site(self, monkeypatch):
        weather = _reload_cog(monkeypatch, WEB_APP_URL="https://weather.example.com")
        assert weather._web_app_link(42) == "https://weather.example.com"

    def test_never_advertises_the_localhost_default(self, monkeypatch):
        """WEB_PUBLIC_URL always has a 127.0.0.1 default, so it must not be a
        fallback here -- a deployment that configured nothing would otherwise tell
        every user to visit their own machine."""
        weather = _reload_cog(monkeypatch)
        assert weather._web_app_link(42) is None

    def test_unconfigured_says_so_instead_of_linking_nowhere(self, monkeypatch):
        weather = _reload_cog(monkeypatch)
        embed, view = weather._web_app_embed(42)
        assert "not configured" in embed.title.lower()
        assert view is None


@pytest.fixture(autouse=True)
def _restore_modules():
    """Leave zephyr.config and the cog bound to the real environment again.

    Both modules are process-wide singletons, so a reloaded copy would otherwise
    leak a patched WEB_PUBLIC_URL into every test that runs after this file.
    """
    yield
    import zephyr.config as config
    import zephyr.cogs.weather as weather

    importlib.reload(config)
    importlib.reload(weather)
