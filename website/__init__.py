"""Flask application factory for Zephyr's public web service.

Load-bearing invariant: **no request handler reads zephyr.config directly.**
This module is the only place that touches it, seeding app.config from it, and
handlers read current_app.config instead. zephyr/config.py calls load_dotenv()
and binds every value at import time, so a fixture cannot undo it -- routing all
configuration through app.config is what makes the tests hermetic no matter what
sits in the developer's .env.

app.secret_key is deliberately never set. Sessions are opaque ids in Redis
(website/session.py) and flask.session is unused, so leaving it unset makes any
accidental use of flask.session fail loudly.
"""

from collections.abc import Mapping

from flask import Flask, jsonify

from zephyr import config


def _defaults() -> dict:
    return {
        "AUTH_ENABLED": config.AUTH_ENABLED,
        "DISCORD_CLIENT_ID": config.DISCORD_CLIENT_ID,
        "DISCORD_CLIENT_SECRET": config.DISCORD_CLIENT_SECRET,
        "DISCORD_REDIRECT_URI": config.DISCORD_REDIRECT_URI,
        "DISCORD_OAUTH_SCOPES": config.DISCORD_OAUTH_SCOPES,
        "DISCORD_INVITE_PERMISSIONS": config.DISCORD_INVITE_PERMISSIONS,
        "WEB_PUBLIC_URL": config.WEB_PUBLIC_URL,
        "REDIS_URL": config.REDIS_URL,
        "DATABASE_URL": config.DATABASE_URL,
        "AUTH_COOKIE_NAME": config.AUTH_COOKIE_NAME,
        "CSRF_COOKIE_NAME": config.CSRF_COOKIE_NAME,
        "OAUTH_STATE_COOKIE_NAME": config.OAUTH_STATE_COOKIE_NAME,
        "AUTH_COOKIE_SECURE": config.AUTH_COOKIE_SECURE,
        "AUTH_SESSION_TTL": config.AUTH_SESSION_TTL,
        "AUTH_SESSION_MAX_AGE": config.AUTH_SESSION_MAX_AGE,
        "OAUTH_STATE_TTL": config.OAUTH_STATE_TTL,
        "GUILDS_FRESH_SECONDS": config.GUILDS_FRESH_SECONDS,
        "SPA_LOGIN_PATH": config.SPA_LOGIN_PATH,
        "SPA_DEFAULT_PATH": config.SPA_DEFAULT_PATH,
        "ENABLED_COGS": list(config.ENABLED_COGS),
        "TRUST_PROXY": config.TRUST_PROXY,
    }


def create_app(overrides: Mapping[str, object] | None = None) -> Flask:
    app = Flask(__name__, static_folder=None)
    app.config.update(_defaults())
    # Applied last so tests and embedders win over the environment.
    app.config.update(overrides or {})

    if app.config["TRUST_PROXY"]:
        # Only when something is actually proxying us: trusting X-Forwarded-For
        # with no proxy in front lets a client spoof its own address, which will
        # matter for the Phase 7 rate limiter.
        from werkzeug.middleware.proxy_fix import ProxyFix

        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    from website.api import api
    from website.spa import spa

    app.register_blueprint(api, url_prefix="/api/v1")
    app.register_blueprint(spa)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
