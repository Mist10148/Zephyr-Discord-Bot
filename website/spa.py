"""Serve the built single-page application without shadowing API errors."""

from pathlib import Path

from flask import Blueprint, current_app, send_from_directory
from werkzeug.security import safe_join

spa = Blueprint("spa", __name__)


def _static_dir() -> Path:
    return Path(current_app.root_path) / "static"


@spa.route("/", defaults={"path": ""})
@spa.route("/<path:path>")
def serve_spa(path: str):
    if path.startswith("api/"):
        from website.api import error

        return error("not_found", "API route not found", 404)
    static_dir = _static_dir()
    # safe_join first: checking is_file() on the raw path answered 404 vs the
    # SPA shell depending on whether a file *outside* static/ existed.
    joined = safe_join(str(static_dir), path) if path else None
    if joined is not None and Path(joined).is_file():
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
