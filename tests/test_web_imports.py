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

# The bot's own import boundary, and the reason it has one: genai.Client()
# validates credentials in its constructor, so building it at module scope made
# importing zephyr.client require a live Gemini key. Every test that touches the
# bot then failed on a machine without one, as a test failure rather than as the
# configuration error it was.
#
# The assertion is "no client was constructed", not "importing without a key
# works". Only the former is checkable on a developer's machine: zephyr.config
# calls load_dotenv(), so a local .env puts the key back into the environment no
# matter what this probe clears first. A client that does not exist cannot have
# demanded a key, which is the property that matters and holds everywhere.
BOT_PROBE = """
import zephyr.services.gemini as gemini
import zephyr.client   # noqa: F401
assert gemini._client is None, "a Gemini client was constructed at import time"
print("BOT_IMPORT_OK")
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

    def test_importing_the_bot_needs_no_gemini_key(self):
        """A subprocess, so the suite's own already-imported modules prove nothing."""
        result = subprocess.run(
            [sys.executable, "-c", BOT_PROBE],
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert "BOT_IMPORT_OK" in result.stdout, result.stdout


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
