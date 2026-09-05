"""Entry point for the Flask weather website.

    python run_web.py

Then open http://localhost:5000
"""

import os

from zephyr import config

# Fail early with a clear message if the OpenWeather key is missing.
config.validate_web_config()

from website.app import app


def main():
    debug = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    app.run(debug=debug, host=config.FLASK_HOST, port=config.PORT)


if __name__ == "__main__":
    main()
