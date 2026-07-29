"""Flask application factory for Zephyr's public web service."""

from flask import Flask, jsonify


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    from website.api import api
    from website.legacy import legacy
    from website.spa import spa

    app.register_blueprint(api, url_prefix="/api/v1")
    app.register_blueprint(legacy)
    app.register_blueprint(spa)

    @app.get("/health")
    def health():
        return jsonify({"status": "ok"})

    return app
