"""WSGI entry point for production servers.

Usage examples:
    gunicorn wsgi:app
    waitress-serve --port 5000 wsgi:app
"""

from zephyr import config

config.validate_web_config()

from website.app import app  # noqa: E402
