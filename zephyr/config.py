"""Central configuration: loads secrets from .env and exposes constants.

All values that were hardcoded at the top of the original ``bot.py`` (and in
``Main.py``) live here. Secrets come from the environment; everything else
(API endpoints, coordinates, model names) stays as plain constants so behavior
is identical to the original.
"""

import sys
import os
from pathlib import Path

from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Force UTF-8 output so emoji don't crash on Windows terminals
# (carried over from the original bot.py, lines 40-44)
# ---------------------------------------------------------------------------
if sys.stdout and sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if sys.stderr and sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# PROJECT_ROOT is the folder that contains this `zephyr` package (i.e. the
# codebase root that also holds ffmpeg/, libopus-0.x64.dll, settings.json, .env).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

load_dotenv(PROJECT_ROOT / ".env")

# ---------------------------------------------------------------------------
# Secrets (from .env)
# ---------------------------------------------------------------------------
TOKEN = os.getenv("DISCORD_TOKEN")
API_KEY = os.getenv("OPENWEATHER_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SPOTIFY_CLIENT_ID = os.getenv("SPOTIFY_CLIENT_ID")
SPOTIFY_CLIENT_SECRET = os.getenv("SPOTIFY_CLIENT_SECRET")

# Optional overrides
FFMPEG_PATH_OVERRIDE = os.getenv("FFMPEG_PATH") or None
WEB_APP_URL = os.getenv("WEB_APP_URL", "https://severely-musical-mollusk.ngrok-free.app/")
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

# Cloud / container overrides
# Render, Heroku, AWS, etc. set PORT for the web process.
PORT = int(os.getenv("PORT") or FLASK_PORT)

# Optional Redis URL for shared settings/history across cloud instances.
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("REDISCLOUD_URL") or None

# Optional custom path for the local settings file (useful for mounted volumes).
SETTINGS_PATH = os.getenv("SETTINGS_PATH") or str(PROJECT_ROOT / "settings.json")

# ---------------------------------------------------------------------------
# Weather API endpoints & coordinates
# ---------------------------------------------------------------------------
CURRENT_URL = "http://api.openweathermap.org/data/2.5/weather"
FORECAST_URL = "http://api.openweathermap.org/data/2.5/forecast"
ALERTS_URL = "http://api.openweathermap.org/data/3.0/onecall"
PHILIPPINE_COORDS = {"lat": 12.8797, "lon": 121.7740}
ILOILO_COORDS = {"lat": 10.7202, "lon": 122.5621}

# ---------------------------------------------------------------------------
# Gemini chat model names
# ---------------------------------------------------------------------------
DEFAULT_CHAT_MODEL = "gemini-3.1-flash-lite"
SECONDARY_CHAT_MODEL = "gemini-2.5-flash-lite"
TERTIARY_CHAT_MODEL = "gemini-2.5-flash"

# ---------------------------------------------------------------------------
# Settings persistence
# ---------------------------------------------------------------------------
# Kept for backwards compatibility; new code should use SETTINGS_PATH.
SETTINGS_FILE = SETTINGS_PATH


def validate_bot_config():
    """Ensure the keys the bot needs are present; raise with a clear message if not."""
    missing = [
        name
        for name, value in (
            ("DISCORD_TOKEN", TOKEN),
            ("OPENWEATHER_API_KEY", API_KEY),
            ("GEMINI_API_KEY", GEMINI_API_KEY),
            ("SPOTIFY_CLIENT_ID", SPOTIFY_CLIENT_ID),
            ("SPOTIFY_CLIENT_SECRET", SPOTIFY_CLIENT_SECRET),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(
            "Missing required environment variable(s): "
            + ", ".join(missing)
            + f".\nAdd them to {PROJECT_ROOT / '.env'} (see .env.example)."
        )


def validate_web_config():
    """Ensure the website has the OpenWeather key it needs."""
    if not API_KEY:
        raise RuntimeError(
            "Missing required environment variable: OPENWEATHER_API_KEY.\n"
            f"Add it to {PROJECT_ROOT / '.env'} (see .env.example)."
        )
