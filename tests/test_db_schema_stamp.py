"""A database built by create_all must agree with Alembic about its version."""

from sqlalchemy import text

from zephyr.db.engine import _head_revision, build_engine, create_schema


def test_a_fresh_database_is_stamped_at_the_migration_head(tmp_path):
    engine = build_engine(f"sqlite:///{tmp_path / 'fresh.db'}")
    create_schema(engine)
    create_schema(engine)  # idempotent: no second row, no error
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT version_num FROM alembic_version")).all()
    assert rows == [(_head_revision(),)]


def test_an_existing_unversioned_database_is_left_for_the_operator(tmp_path):
    """Tables but no alembic_version: stamping blindly could skip real migrations."""
    engine = build_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as connection:
        connection.execute(text("CREATE TABLE playlists (id INTEGER PRIMARY KEY)"))
    create_schema(engine)
    with engine.connect() as connection:
        tables = {row[0] for row in connection.execute(text("SELECT name FROM sqlite_master WHERE type='table'"))}
    assert "alembic_version" not in tables
