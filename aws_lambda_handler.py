"""AWS Lambda entry point for the Flask weather website.

Use this handler with API Gateway (HTTP or REST). It expects ``awsgi`` to be
installed (listed in requirements.txt).
"""

from apig_wsgi import make_lambda_handler

from zephyr import config

config.validate_web_config()

from website.app import app  # noqa: E402


lambda_handler = make_lambda_handler(app)
