"""Vercel serverless entry point for the Flask weather website.

Vercel looks for an ``app`` callable in the file referenced by vercel.json.
"""

from zephyr import config

config.validate_web_config()

from website.app import app  # noqa: E402
