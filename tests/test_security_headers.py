"""Tests for the response security headers.

These are cheap to get wrong in a way nothing else notices: a CSP that forgets an
image host does not fail a build, it just renders a blank guild picker in production.
"""

import pytest

from website.security import CSP, HEADERS


def _directive(name: str) -> str:
    for part in CSP.split(";"):
        part = part.strip()
        if part.startswith(name + " "):
            return part
    raise AssertionError(f"CSP has no {name} directive: {CSP}")


class TestCsp:
    def test_allows_discord_avatars_and_guild_icons(self):
        """Without this the dashboard's guild picker renders blank."""
        assert "https://cdn.discordapp.com" in _directive("img-src")

    def test_allows_the_track_artwork_hosts(self):
        """Wildcarded because yt-dlp returns whichever CDN shard it was given --
        i.ytimg.com, i9.ytimg.com and yt3.ggpht.com all turn up, and a missed
        shard is a silently broken now-playing image."""
        img = _directive("img-src")
        assert "https://*.ytimg.com" in img
        assert "https://*.ggpht.com" in img
        assert "https://*.scdn.co" in img

    def test_allows_data_uris_for_inlined_images(self):
        assert "data:" in _directive("img-src")

    def test_scripts_are_same_origin_only(self):
        """No 'unsafe-inline': an injected inline script must not be able to run."""
        script = _directive("script-src")
        assert "'self'" in script
        assert "unsafe-inline" not in script
        assert "unsafe-eval" not in script

    def test_styles_allow_inline_because_react_sets_style_attributes(self):
        assert "'unsafe-inline'" in _directive("style-src")

    def test_framing_and_objects_are_denied(self):
        assert "'none'" in _directive("frame-ancestors")
        assert "'none'" in _directive("object-src")

    def test_has_a_default_src_fallback(self):
        assert "'self'" in _directive("default-src")


class TestHeadersOnResponses:
    def test_applied_to_the_api(self, client):
        response = client.get("/api/v1/status")
        assert "Content-Security-Policy" in response.headers
        assert response.headers["X-Content-Type-Options"] == "nosniff"
        assert response.headers["X-Frame-Options"] == "DENY"
        assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"

    def test_applied_to_health(self, client):
        assert "Content-Security-Policy" in client.get("/health").headers

    def test_applied_to_error_responses(self, client):
        """An error page is as injectable as any other; headers must not be skipped."""
        response = client.get("/api/v1/nope")
        assert response.status_code == 404
        assert "Content-Security-Policy" in response.headers

    def test_applied_when_the_dashboard_is_not_configured(self, public_app):
        response = public_app.test_client().get("/health")
        assert "Content-Security-Policy" in response.headers

    def test_does_not_clobber_a_header_a_view_already_set(self):
        """setdefault, so guard.py's per-request cache headers still win."""
        from website import create_app

        app = create_app({"TESTING": True})

        @app.get("/custom")
        def custom():
            return "ok", 200, {"Referrer-Policy": "no-referrer"}

        assert app.test_client().get("/custom").headers["Referrer-Policy"] == "no-referrer"


class TestHsts:
    def test_not_sent_over_plain_http(self):
        """Pinning localhost to https in the browser for a year would be self-harm."""
        from website import create_app

        app = create_app({"TESTING": True, "WEB_PUBLIC_URL": "http://localhost",
                          "FORCE_HTTPS_HEADERS": False})
        assert "Strict-Transport-Security" not in app.test_client().get("/health").headers

    def test_sent_for_an_https_origin(self):
        from website import create_app

        app = create_app({"TESTING": True, "WEB_PUBLIC_URL": "https://zephyr.example.com",
                          "FORCE_HTTPS_HEADERS": True})
        header = app.test_client().get("/health").headers["Strict-Transport-Security"]
        assert "max-age=31536000" in header

    def test_derived_from_the_public_origin_by_default(self, monkeypatch):
        import importlib

        import zephyr.config as config

        monkeypatch.setenv("WEB_PUBLIC_URL", "https://zephyr.example.com")
        monkeypatch.setattr("zephyr.config.load_dotenv", lambda *a, **k: None, raising=False)
        importlib.reload(config)
        try:
            from website import create_app

            assert create_app({"TESTING": True}).config["FORCE_HTTPS_HEADERS"] is True
        finally:
            monkeypatch.delenv("WEB_PUBLIC_URL", raising=False)
            importlib.reload(config)


class TestHeadersTable:
    def test_every_expected_header_is_present(self):
        assert set(HEADERS) == {
            "Content-Security-Policy",
            "X-Content-Type-Options",
            "X-Frame-Options",
            "Referrer-Policy",
            "Permissions-Policy",
            "Cross-Origin-Opener-Policy",
        }


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
