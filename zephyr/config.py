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
# The link /use prints. Previously this defaulted to a hardcoded ngrok host belonging
# to one developer's tunnel, so every fork and every deployment that forgot to set it
# advertised a stranger's dead URL to its users. No default is better than a wrong
# one: /use reports that it is unconfigured instead.
WEB_APP_URL = os.getenv("WEB_APP_URL") or None
FLASK_HOST = os.getenv("FLASK_HOST", "0.0.0.0")
FLASK_PORT = int(os.getenv("FLASK_PORT", "5000"))

# Cloud / container overrides
# Render, Heroku, AWS, etc. set PORT for the web process.
PORT = int(os.getenv("PORT") or FLASK_PORT)

# Optional Redis URL for shared settings/history across cloud instances.
REDIS_URL = os.getenv("REDIS_URL") or os.getenv("REDISCLOUD_URL") or None

# Database-backed settings are preferred when explicitly configured.  A local
# SQLite database remains the zero-configuration default for development.
DATABASE_URL = os.getenv("DATABASE_URL") or None
DEFAULT_DATABASE_URL = f"sqlite:///{(PROJECT_ROOT / 'data' / 'zephyr.db').as_posix()}"
STORAGE_BACKEND = (os.getenv("STORAGE_BACKEND") or "auto").lower()
DB_ECHO = os.getenv("DB_ECHO", "0").lower() in {"1", "true", "yes"}
DB_AUTO_CREATE = os.getenv("DB_AUTO_CREATE", "1").lower() in {"1", "true", "yes"}

# Optional custom path for the local settings file (useful for mounted volumes).
SETTINGS_PATH = os.getenv("SETTINGS_PATH") or str(PROJECT_ROOT / "settings.json")

# ---------------------------------------------------------------------------
# Web dashboard (Discord OAuth)
# ---------------------------------------------------------------------------
DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID") or None
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET") or None

# Public origin of the dashboard.  Render injects RENDER_EXTERNAL_URL for us.
# WEB_APP_URL is deliberately NOT consulted here even though it looks similar: it is
# the /use link, which may legitimately point at a different host (or nowhere), and a
# redirect_uri that does not byte-match the Discord application fails with an opaque
# invalid_redirect_uri.  Keep the two independent.
WEB_PUBLIC_URL = (
    os.getenv("WEB_PUBLIC_URL") or os.getenv("RENDER_EXTERNAL_URL") or f"http://127.0.0.1:{PORT}"
).rstrip("/")

# Must byte-match a redirect registered in the Discord Developer Portal, so it is
# never derived from request headers (no url_for(_external=True)).
OAUTH_CALLBACK_PATH = "/api/v1/auth/callback"
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI") or f"{WEB_PUBLIC_URL}{OAUTH_CALLBACK_PATH}"
DISCORD_OAUTH_SCOPES = "identify guilds"

# Session cookie.  AUTH_COOKIE_* rather than Flask's own SESSION_COOKIE_*: this
# is a bespoke server-side store and flask.session stays unused.
AUTH_COOKIE_NAME = os.getenv("AUTH_COOKIE_NAME", "zephyr_session")
CSRF_COOKIE_NAME = os.getenv("CSRF_COOKIE_NAME", "zephyr_csrf")
OAUTH_STATE_COOKIE_NAME = "zephyr_oauth_state"
AUTH_COOKIE_SECURE = (
    os.getenv("AUTH_COOKIE_SECURE") or ("1" if WEB_PUBLIC_URL.startswith("https://") else "0")
).lower() in {"1", "true", "yes"}

# Sliding session lifetime, plus a hard cap that no amount of activity extends.
AUTH_SESSION_TTL = int(os.getenv("SESSION_TTL_SECONDS", str(7 * 24 * 3600)))
AUTH_SESSION_MAX_AGE = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(30 * 24 * 3600)))
OAUTH_STATE_TTL = 600

# How long a session's cached guild list is trusted before /me flags it stale.
GUILDS_FRESH_SECONDS = int(os.getenv("GUILDS_FRESH_SECONDS", "3600"))

# SPA paths the OAuth endpoints redirect back into.
SPA_LOGIN_PATH = "/login"
SPA_DEFAULT_PATH = "/g"

# Only trust X-Forwarded-* when something is actually proxying us; Render sets RENDER.
TRUST_PROXY = (os.getenv("TRUST_PROXY_HEADERS") or ("1" if os.getenv("RENDER") else "0")).lower() in {
    "1",
    "true",
    "yes",
}

# Permissions bitfield used to build the "Add Zephyr" invite link.
DISCORD_INVITE_PERMISSIONS = os.getenv("DISCORD_INVITE_PERMISSIONS", "3197952")

# Single source of truth for the cog list: the bot loads these, and the web tier
# reports them as a guild's default enabled_cogs without importing the client.
ENABLED_COGS = ("weather", "music", "voice_tts", "chat", "help")

# The dashboard needs an OAuth application *and* Redis (sessions are shared
# across gunicorn workers).  Without all three, only the public weather site runs.
AUTH_ENABLED = bool(DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET and REDIS_URL)

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


def _normalize_database_url(url: str | None) -> str | None:
    """Return a SQLAlchemy 2 compatible URL for the configured database."""
    if not url:
        return None
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url.removeprefix("postgres://")
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url.removeprefix("postgresql://")
    return url


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
    """Validate the dashboard's OAuth wiring; a weather-only deployment needs nothing.

    Only *partial* configuration raises.  Leaving the dashboard unconfigured is a
    supported deployment (the public weather site), but half-configuring it is
    always a mistake -- a typo'd variable name, or credentials with nowhere to keep
    the sessions.

    REDIS_URL is deliberately not treated as a signal of intent.  It predates the
    dashboard and still has an independent job (shared AI settings), so a Redis
    instance attached to the web service must not by itself demand OAuth
    credentials -- otherwise wiring up Redis takes the whole site down until
    somebody fills in two secrets.
    """
    credentials = {"DISCORD_CLIENT_ID": DISCORD_CLIENT_ID, "DISCORD_CLIENT_SECRET": DISCORD_CLIENT_SECRET}
    provided = [name for name, value in credentials.items() if value]
    missing = [name for name, value in credentials.items() if not value]
    if provided and missing:
        raise RuntimeError(
            "The web dashboard is only partially configured. Missing: "
            + ", ".join(missing)
            + f".\nAdd it to {PROJECT_ROOT / '.env'} (see .env.example), or unset "
            + ", ".join(provided)
            + " to serve the public weather site only."
        )
    if provided and not REDIS_URL:
        raise RuntimeError(
            "The web dashboard needs REDIS_URL: sessions are server-side and shared "
            "across workers, so they cannot be held in process memory.\n"
            f"Add it to {PROJECT_ROOT / '.env'} (see .env.example), or unset "
            "DISCORD_CLIENT_ID and DISCORD_CLIENT_SECRET to serve the public weather site only."
        )
    if AUTH_ENABLED and not DISCORD_REDIRECT_URI.startswith(("http://", "https://")):
        raise RuntimeError(f"DISCORD_REDIRECT_URI must be an absolute URL (got {DISCORD_REDIRECT_URI}).")
    if AUTH_ENABLED and not DISCORD_REDIRECT_URI.endswith(OAUTH_CALLBACK_PATH):
        raise RuntimeError(
            f"DISCORD_REDIRECT_URI must end with {OAUTH_CALLBACK_PATH} (got {DISCORD_REDIRECT_URI})."
        )
