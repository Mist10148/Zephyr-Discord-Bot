"""WSGI entry point for production servers.

Usage examples:
    gunicorn wsgi:app
    waitress-serve --port 5000 wsgi:app
"""

from zephyr import config

config.validate_web_config()

from zephyr.core.logging import configure_logging  # noqa: E402

# gunicorn imports this once per worker; configure_logging is idempotent.
configure_logging(service="web")

from website.app import app  # noqa: E402
