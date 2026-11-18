"""Lazy SQLAlchemy engine construction."""

from pathlib import Path
from typing import Any

from sqlalchemy import Column, Engine, MetaData, String, Table, create_engine, inspect

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


MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def _head_revision() -> str | None:
    try:
        from alembic.script import ScriptDirectory

        return ScriptDirectory(str(MIGRATIONS_DIR)).get_current_head()
    except Exception as exc:  # alembic missing or the scripts unreadable
        print(f"[DB] Could not read the migration head: {exc}")
        return None


def create_schema(engine: Engine) -> None:
    """Create the schema when it does not exist yet.

    A database built from nothing is also stamped at the migration head.
    ``create_all`` wrote every table but no ``alembic_version`` row, so the
    documented ``alembic upgrade head`` then failed at the first revision with
    "table already exists" (and ``stamp 0001`` failed at the second).
    """
    existing = set(inspect(engine).get_table_names())
    fresh = not (existing & set(Base.metadata.tables)) and "alembic_version" not in existing
    Base.metadata.create_all(engine)
    if fresh:
        head = _head_revision()
        if head:
            version = Table("alembic_version", MetaData(), Column("version_num", String(32), primary_key=True))
            with engine.begin() as connection:
                version.create(connection, checkfirst=True)
                connection.execute(version.insert().values(version_num=head))
