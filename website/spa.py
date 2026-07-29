"""Serve the built single-page application without shadowing API errors."""

from pathlib import Path

from flask import Blueprint, current_app, send_from_directory

spa = Blueprint("spa", __name__)


def _static_dir() -> Path:
    return Path(current_app.root_path) / "static"


@spa.route("/<path:path>")
def serve_spa(path: str):
    if path.startswith("api/"):
        from website.api import error

        return error("not_found", "API route not found", 404)
    static_dir = _static_dir()
    candidate = static_dir / path
    if candidate.is_file():
        response = send_from_directory(static_dir, path)
        if path.startswith("assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response
    index = static_dir / "index.html"
    if not index.is_file():
        return (
            {"error": {"code": "spa_not_built", "message": "Build the SPA with npm --prefix website/frontend run build", "detail": None}},
            503,
        )
    response = send_from_directory(static_dir, "index.html")
    response.headers["Cache-Control"] = "no-cache"
    return response
