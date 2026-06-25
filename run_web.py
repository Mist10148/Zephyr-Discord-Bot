"""Entry point for the Flask weather website.

    python run_web.py

Then open http://localhost:5000
"""

from zephyr import config

# Fail early with a clear message if the OpenWeather key is missing.
config.validate_web_config()

from website.app import app


def main():
    app.run(debug=True, host=config.FLASK_HOST, port=config.FLASK_PORT)


if __name__ == "__main__":
    main()
