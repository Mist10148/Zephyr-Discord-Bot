"""SPA routing, robots and the sitemap.

There was no test file for spa.py at all, and no route table on the Python side:
every unknown path answered `index.html` with HTTP **200**, so `/nonsense` was
an indexable soft 404 that rendered the NotFound screen. A crawler finding a
hundred 200s all saying "page not found" is being told the site has a hundred
pages.
"""

import re
from pathlib import Path

import pytest

from website import routes


@pytest.fixture
def site(fake_redis):
    from website import create_app

    app = create_app({
        "TESTING": True,
        "AUTH_ENABLED": False,
        "WEB_PUBLIC_URL": "https://zephyr.example",
    })
    return app.test_client()


class TestKnownRoutes:
    @pytest.mark.parametrize("path", [
        "/", "/weather", "/commands", "/privacy", "/terms", "/settings",
        "/kitchen-sink", "/login", "/g",
    ])
    def test_a_real_route_answers_200(self, site, path):
        assert site.get(path).status_code == 200

    @pytest.mark.parametrize("path", [
        "/g/123456789012345678",
        "/g/123456789012345678/music",
        "/g/123456789012345678/weather-alerts",
        "/g/123456789012345678/ai",
        "/g/123456789012345678/settings",
        "/g/123456789012345678/audit",
    ])
    def test_a_guild_sub_page_answers_200(self, site, path):
        assert site.get(path).status_code == 200

    def test_a_trailing_slash_is_the_same_route(self, site):
        assert site.get("/weather/").status_code == 200


class TestUnknownRoutes:
    @pytest.mark.parametrize("path", [
        "/nonsense", "/weather/extra", "/g/notanid", "/g/123/unknown-tab",
        "/wp-admin", "/.env",
    ])
    def test_it_answers_404(self, site, path):
        assert site.get(path).status_code == 404

    def test_it_still_returns_the_shell_so_the_app_can_render_NotFound(self, site):
        """abort(404) would hit the app-wide API error handler and answer a
        browser navigation with a JSON envelope. Returning the shell *with* a
        404 status means a person gets a real page and a crawler gets the right
        status code."""
        response = site.get("/nonsense")
        assert response.status_code == 404
        assert response.mimetype == "text/html"
        assert b'id="root"' in response.data

    def test_an_api_path_is_still_a_json_envelope(self, site):
        response = site.get("/api/v1/no-such-thing")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"]


class TestRobots:
    def test_it_exists(self, site):
        response = site.get("/robots.txt")
        assert response.status_code == 200
        assert response.mimetype == "text/plain"

    def test_it_disallows_the_private_routes_and_the_api(self, site):
        body = site.get("/robots.txt").get_data(as_text=True)
        for route in ("/kitchen-sink", "/login", "/g", "/api/"):
            assert f"Disallow: {route}" in body

    def test_it_points_at_the_sitemap(self, site):
        body = site.get("/robots.txt").get_data(as_text=True)
        assert "Sitemap: https://zephyr.example/sitemap.xml" in body


class TestSitemap:
    def test_it_lists_the_public_routes(self, site):
        body = site.get("/sitemap.xml").get_data(as_text=True)
        for route in ("/weather", "/commands", "/privacy", "/terms"):
            assert f"https://zephyr.example{route}" in body

    def test_it_omits_the_private_ones(self, site):
        body = site.get("/sitemap.xml").get_data(as_text=True)
        for route in ("/kitchen-sink", "/login", "/g"):
            assert f"<loc>https://zephyr.example{route}</loc>" not in body

    def test_it_uses_the_configured_origin_not_the_request_host(self, site):
        """request.host_url would put an internal hostname into a sitemap
        whenever something reached the app by another route."""
        body = site.get("/sitemap.xml", base_url="http://internal.local").get_data(as_text=True)
        assert "internal.local" not in body
        assert "zephyr.example" in body

    def test_it_is_well_formed(self, site):
        from xml.etree import ElementTree

        ElementTree.fromstring(site.get("/sitemap.xml").get_data())


class TestTheTwoRouteTablesAgree:
    """website/routes.py and App.tsx are the same list in two languages, and
    Flask cannot read the other one. If they drift, either a real route 404s or
    an unknown path answers 200 again."""

    def _app_tsx_routes(self) -> set[str]:
        source = (
            Path(__file__).resolve().parent.parent
            / "website" / "frontend" / "src" / "App.tsx"
        ).read_text(encoding="utf-8")
        found = set(re.findall(r'<Route path="([^"]+)"', source))
        return {path for path in found if path != "*"}

    def test_every_frontend_route_is_known_to_flask(self):
        for path in self._app_tsx_routes():
            # Substitute a plausible id for the parameter.
            concrete = path.replace(":guildId", "123456789012345678")
            assert routes.is_known(concrete), path

    def test_flask_knows_no_route_the_frontend_does_not_render(self):
        declared = self._app_tsx_routes()
        patterns = {path.replace(":guildId", "123456789012345678") for path in declared}
        for route in (*routes.PUBLIC_ROUTES, *routes.PRIVATE_ROUTES):
            assert route in patterns, route


class TestIndexability:
    def test_only_the_public_routes_are_indexable(self):
        assert routes.is_indexable("/weather") is True
        assert routes.is_indexable("/privacy") is True
        # Behind auth or internal: a crawler gets an empty shell, and indexing
        # that is worse than not indexing it.
        assert routes.is_indexable("/kitchen-sink") is False
        assert routes.is_indexable("/login") is False
        assert routes.is_indexable("/g/123/music") is False
