"""Entry point for the Flask weather website.

    python run_web.py

Then open http://127.0.0.1:5000 -- the same host as the default
DISCORD_REDIRECT_URI, or the OAuth state cookie will not survive the round trip.
Build the dashboard first with ``npm --prefix website/frontend run build``.
"""

import os

from zephyr import config

# Fail early with a clear message if the dashboard configuration is half-set.
config.validate_web_config()

from website.app import app


def main():
    debug = os.getenv("FLASK_DEBUG", "0").lower() in ("1", "true", "yes")
    host = config.FLASK_HOST
    if debug and not os.getenv("FLASK_HOST"):
        # The Werkzeug debugger runs arbitrary code; never expose it to the
        # network just because FLASK_HOST defaults to 0.0.0.0.
        host = "127.0.0.1"
    app.run(debug=debug, host=host, port=config.PORT)


if __name__ == "__main__":
    main()
