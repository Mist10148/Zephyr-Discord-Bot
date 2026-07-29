"""Versioned JSON API foundation."""

from flask import Blueprint, jsonify

api = Blueprint("api", __name__)


def error(code: str, message: str, status: int, detail: object | None = None):
    payload = {"error": {"code": code, "message": message, "detail": detail}}
    return jsonify(payload), status


@api.app_errorhandler(404)
def api_not_found(_error):
    return error("not_found", "API route not found", 404)


@api.app_errorhandler(405)
def api_method_not_allowed(_error):
    return error("method_not_allowed", "Method not allowed", 405)


# Register endpoint modules after the blueprint and shared helpers exist.
# `guard` belongs in this list too: its before_request/after_request hooks only
# attach when the module is imported.
from website.api import auth, commands, guard, weather  # noqa: E402,F401
