"""Compatibility shim for existing WSGI and local entry points."""

from website import create_app

app = create_app()
