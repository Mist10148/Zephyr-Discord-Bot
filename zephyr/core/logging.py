"""Logging configuration for both processes.

``logging`` was not imported anywhere in this codebase.  There were 65 ``print``
calls, and the ~47 that sat inside ``except`` blocks nearly all printed
``str(e)`` -- so the stack was discarded at the point of failure and what
reached the operator was a bare sentence with no timestamp, no level, no module
and no line number.  That is the root cause of the project's debuggability
problem: the slash-command error handler has nowhere to put a traceback, error
tracking has nothing to ship, and a Render log stream cannot be filtered.

Two formats.  Locally, one readable line per record.  In the cloud, JSON, so a
log platform can index the level and the logger name instead of regex-matching
prose.  ``LOG_FORMAT`` overrides the choice; otherwise the presence of ``RENDER``
decides, following the same convention ``TRUST_PROXY`` already uses in config.

Deliberately no ``structlog`` or ``python-json-logger``.  The formatter below is
twenty lines, and ``tests/conftest.py`` sets the precedent for preferring a small
amount of hand-written code over a dependency that has to be installed
everywhere the suite runs.
"""

import json
import logging
import sys
import time

from zephyr.config import LOG_FORMAT, LOG_LEVEL

PLAIN_FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
PLAIN_DATEFMT = "%Y-%m-%d %H:%M:%S"

# Everything LogRecord defines. Anything else on a record was put there by a
# caller through `extra=`, and is worth carrying into the payload.
_RESERVED = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None)))| {
    "asctime", "message", "taskName",
}

_configured = False


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the traceback as a string field.

    A multi-line traceback in a line-oriented log platform becomes N unrelated
    entries, only the first of which carries the level and the logger -- so the
    exception is folded into the record rather than appended after it.
    """

    converter = time.gmtime

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S") + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        # default=str so a caller passing an arbitrary object through `extra=`
        # degrades to its repr rather than losing the whole record.
        return json.dumps(payload, default=str)


def configure_logging(*, service: str, level: str | None = None, json_output: bool | None = None) -> None:
    """Install one handler on the root logger.  Safe to call more than once.

    ``service`` is attached to every record, because both processes write to the
    same log stream on Render and "which one said this" is the first question.

    Idempotent on purpose: gunicorn imports ``wsgi`` once per worker, and
    ``run_web`` is importable from a test.  A second call would otherwise double
    every line.
    """
    global _configured
    if _configured:
        return

    resolved_level = (level or LOG_LEVEL).upper()
    use_json = LOG_FORMAT == "json" if json_output is None else json_output

    # stderr, not stdout: config.py reconfigures both to UTF-8, and keeping
    # diagnostics off stdout leaves the startup banner (which is UI, not
    # logging) legible on its own stream.
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter() if use_json else logging.Formatter(PLAIN_FORMAT, PLAIN_DATEFMT))
    handler.addFilter(_ServiceFilter(service))

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(getattr(logging, resolved_level, logging.INFO))

    # discord.py logs one line per gateway event at DEBUG and its HTTP client is
    # chatty at INFO. Neither is useful unless the gateway itself is the
    # suspect, and both would bury everything Zephyr says.
    logging.getLogger("discord").setLevel(logging.WARNING)
    logging.getLogger("discord.http").setLevel(logging.WARNING)
    logging.getLogger("werkzeug").setLevel(logging.WARNING)

    _configured = True


class _ServiceFilter(logging.Filter):
    def __init__(self, service: str):
        super().__init__()
        self.service = service

    def filter(self, record: logging.LogRecord) -> bool:
        record.service = self.service
        return True


def get_logger(name: str) -> logging.Logger:
    """The module's logger.  Call as ``get_logger(__name__)``."""
    return logging.getLogger(name)


def reset_logging() -> None:
    """Undo ``configure_logging``.  For tests only."""
    global _configured
    _configured = False
    logging.getLogger().handlers = []
