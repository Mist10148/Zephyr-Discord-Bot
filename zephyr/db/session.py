"""Process-wide lazy engine cache.

This module exists so the web tier can reach the database without importing
``zephyr.services.storage``, whose module-level ``storage = get_storage()``
singleton connects, runs ``create_all()`` and executes ``SELECT 1`` at *import*
time.  Building a Flask app must never touch the network.

Access stays on SQLAlchemy Core, like the rest of the codebase -- there is no
sessionmaker anywhere in Zephyr yet, and Phase 3 needs one upsert and one select.
"""

import threading

from sqlalchemy import Engine
from sqlalchemy.exc import SQLAlchemyError

from zephyr.config import DATABASE_URL, DB_AUTO_CREATE, DEFAULT_DATABASE_URL
from zephyr.db.engine import build_engine, create_schema

_lock = threading.Lock()
_engines: dict[str, Engine] = {}


def get_engine(url: str | None = None) -> Engine:
    """Return the cached engine for ``url``, creating the schema on first use.

    ``create_schema`` runs here rather than in ``create_app`` so that constructing
    an application never opens a connection.  Both gunicorn workers may race the
    first call; ``create_all`` is emitted with ``checkfirst``, but Postgres still
    has a narrow window where two concurrent ``CREATE TABLE``s collide, so a
    failure is logged and swallowed -- the query that follows will surface any
    genuine problem.
    """
    target = url or DATABASE_URL or DEFAULT_DATABASE_URL
    with _lock:
        engine = _engines.get(target)
        if engine is not None:
            return engine
        engine = build_engine(target)
        if DB_AUTO_CREATE:
            try:
                create_schema(engine)
            except SQLAlchemyError as exc:
                print(f"[DB] create_schema skipped: {exc}")
        _engines[target] = engine
        return engine


def dispose_engines() -> None:
    """Dispose every cached engine.  Used by tests and by graceful shutdown."""
    with _lock:
        for engine in _engines.values():
            engine.dispose()
        _engines.clear()
