"""Alembic environment.

The URL comes from Zephyr's own config rather than alembic.ini so that
``alembic upgrade head`` always targets the same database the application does,
including the postgres:// -> postgresql+psycopg:// rewrite.

Note the deliberate overlap with ``create_schema()``: development and the
Docker/Render default still create tables via ``Base.metadata.create_all()``,
while migrations are the supported path for an existing deployed database.  That
means the two can drift; a model-vs-migration drift check is a Phase 7 item.
"""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from zephyr.config import DATABASE_URL, DEFAULT_DATABASE_URL, _normalize_database_url
from zephyr.db.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

config.set_main_option("sqlalchemy.url", _normalize_database_url(DATABASE_URL or DEFAULT_DATABASE_URL))


def run_migrations_offline() -> None:
    """Emit SQL to stdout without connecting."""
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live connection."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            # SQLite cannot ALTER most things in place; batch mode rewrites the
            # table instead.  Harmless on Postgres.
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
