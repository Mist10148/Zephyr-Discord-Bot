"""Versioned JSON API foundation."""

from flask import Blueprint, jsonify

api = Blueprint("api", __name__)


def error(code: str, message: str, status: int, detail: object | None = None):
    payload = {"error": {"code": code, "message": message, "detail": detail}}
    return jsonify(payload), status


# Postgres INTEGER, which every autoincrement id here is.
MAX_ROW_ID = 2**31 - 1


def is_snowflake(value) -> bool:
    """True for a Discord id: 1-20 ASCII digits.

    str.isdigit() alone is not enough -- it accepts "²" and other Unicode
    digits that int() then refuses, which turned bad input into 500s.
    """
    text = str(value) if value is not None else ""
    return 0 < len(text) <= 20 and text.isascii() and text.isdigit()


def is_row_id(value) -> bool:
    """True for a database row id that fits the column it will be compared to."""
    text = str(value) if value is not None else ""
    return 0 < len(text) <= 10 and text.isascii() and text.isdigit() and int(text) <= MAX_ROW_ID


@api.app_errorhandler(404)
def api_not_found(_error):
    return error("not_found", "API route not found", 404)


@api.app_errorhandler(405)
def api_method_not_allowed(_error):
    return error("method_not_allowed", "Method not allowed", 405)


@api.errorhandler(Exception)
def api_unhandled(exc):
    """Keep the JSON envelope for failures nobody anticipated.

    Without this a database outage in, say, the settings page came back as
    Flask's HTML 500 and the SPA could only say "http_error".  HTTPExceptions
    (abort(400) and friends) pass through with their own status.
    """
    from werkzeug.exceptions import HTTPException

    if isinstance(exc, HTTPException):
        return error(exc.name.lower().replace(" ", "_"), exc.description or exc.name, exc.code or 500)
    import traceback

    traceback.print_exc()
    return error("internal_error", "Something went wrong on our side.", 500)


# Register endpoint modules after the blueprint and shared helpers exist.
# `guard` belongs in this list too: its before_request/after_request hooks only
# attach when the module is imported.
from website.api import (  # noqa: E402,F401
    auth,
    ai,
    commands,
    guard,
    guilds,
    me,
    player,
    playlists,
    weather,
    weather_subs,
)
