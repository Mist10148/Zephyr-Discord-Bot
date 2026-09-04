"""Serve the built single-page application without shadowing API errors."""

from pathlib import Path

from flask import Blueprint, Response, current_app, request, send_from_directory

from website import routes

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

    # A real 404 for a path the SPA does not render. Previously every unknown
    # path answered 200 with the shell, so /nonsense was an indexable soft 404
    # that rendered NotFound -- and a crawler finding a hundred 200s all saying
    # "page not found" is being told the site has a hundred pages.
    #
    # The *shell* is still returned rather than abort(404), for two reasons:
    # abort would hit the app-wide @api.app_errorhandler(404) in
    # website/api/__init__.py and answer a browser navigation with a JSON
    # envelope; and returning the shell with a 404 status means the SPA still
    # renders its own NotFound screen, so a person gets a real page while a
    # crawler gets the right status code.
    if not routes.is_known(path):
        response.status_code = 404
    return response


@spa.get("/robots.txt")
def robots_txt():
    """Absent before, so nothing was disallowed -- including /login and the
    design-system page."""
    return Response(routes.robots(_origin()), mimetype="text/plain")


@spa.get("/sitemap.xml")
def sitemap_xml():
    return Response(routes.sitemap(_origin()), mimetype="application/xml")


def _origin() -> str:
    """The public origin, from config rather than from the request.

    WEB_PUBLIC_URL is what the deployment actually answers on; using
    request.host_url would put an internal hostname into a sitemap whenever
    something reached the app by another route.
    """
    return current_app.config.get("WEB_PUBLIC_URL") or request.host_url.rstrip("/")
