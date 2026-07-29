"""Lazy SQLAlchemy engine construction."""

from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine

from zephyr.config import DB_ECHO, _normalize_database_url
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
