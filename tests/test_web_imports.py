"""Guards the web tier's import boundary and its no-config boot path.

zephyr/services/storage.py builds a `storage = get_storage()` singleton at import
time, which connects, runs create_all() and executes SELECT 1. If anything under
website/ ever imports it, building a Flask app starts touching the network -- and
the failure shows up as slow or flaky tests rather than as an obvious error. This
file is the only thing that will stop somebody adding a convenient
`from zephyr.services.storage import storage` in six months.
"""

import subprocess
import sys

import pytest

# Run in a subprocess: the rest of the suite legitimately imports these modules,
# so sys.modules in this process proves nothing.
PROBE = """
import sys
from website import create_app
app = create_app({"TESTING": True})
client = app.test_client()
assert client.get("/health").status_code == 200
leaked = [name for name in ("zephyr.services.storage", "zephyr.client") if name in sys.modules]
print("LEAKED:" + ",".join(leaked))
"""


class TestImportBoundary:
    def test_building_an_app_does_not_import_the_storage_singleton(self):
        result = subprocess.run(
            [sys.executable, "-c", PROBE],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "LEAKED:\n" in result.stdout or result.stdout.strip().endswith("LEAKED:"), result.stdout


class TestNoConfigBoot:
    def test_the_public_weather_deployment_still_boots(self, public_app):
        """No OAuth application, no Redis: the weather site must be unaffected."""
        client = public_app.test_client()
        assert client.get("/health").get_json() == {"status": "ok"}

    def test_create_app_takes_no_arguments(self, monkeypatch):
        from website import create_app

        app = create_app()
        assert app.config["AUTH_ENABLED"] is False

    def test_unknown_api_routes_still_return_the_json_envelope(self, public_app):
        response = public_app.test_client().get("/api/v1/nope")
        assert response.status_code == 404
        assert response.get_json()["error"]["code"] == "not_found"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
