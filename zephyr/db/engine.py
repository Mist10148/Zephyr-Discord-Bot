"""Lazy SQLAlchemy engine construction."""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine

from zephyr.config import DB_AUTO_CREATE, DB_ECHO, _normalize_database_url
from zephyr.db.models import Base


def build_engine(url: str, *, echo: bool | None = None) -> Engine:
    """Create a synchronous database engine without opening a connection."""
    normalized = _normalize_database_url(url)
    if not normalized:
        raise ValueError("A database URL is required")

    options: dict[str, Any] = {"pool_pre_ping": True, "echo": DB_ECHO if echo is None else echo}
    if normalized.startswith("sqlite"):
        database = normalized.removeprefix("sqlite:///")
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
        options["connect_args"] = {"check_same_thread": False}
    else:
        options.update({"pool_recycle": 300, "pool_size": 2, "max_overflow": 3})
    return create_engine(normalized, **options)


def create_schema(engine: Engine) -> None:
    """Create the small Phase 0 schema when it does not exist yet."""
    Base.metadata.create_all(engine)


def should_auto_create(url: str) -> bool:
    """Whether ``create_all`` may build the schema for ``url``.

    Two schema paths were live at once: ``alembic upgrade head`` runs from
    render.yaml's preDeployCommand, and then the application called
    ``create_all`` again at boot.  On Render that is merely redundant, because
    Alembic goes first.  Everywhere else it is a trap -- the Dockerfile,
    docker-compose and the Procfile run no migrations at all, so those databases
    get their tables from ``create_all`` while ``alembic_version`` stays pinned
    wherever it was.  The next ``alembic upgrade head`` on such a database then
    dies on "table already exists", and ``op.create_table`` has no checkfirst.

    So: SQLite may still create itself, because a developer cloning the repo
    should not have to run a migration to get a database, and the test suite
    depends on it.  A configured server database is Alembic's alone.

    ``DB_AUTO_CREATE`` remains an explicit override in both directions for the
    case this heuristic gets wrong -- a throwaway Postgres in CI, say.
    """
    if DB_AUTO_CREATE is not None:
        return DB_AUTO_CREATE
    return (_normalize_database_url(url) or "").startswith("sqlite")
