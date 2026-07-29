"""Tests for the Phase 3 database schema and its Alembic baseline.

Everything runs against a throwaway SQLite file, so there are no network calls
and no Postgres.

Note the file database: sqlite:///:memory: cannot be used here.  build_engine
passes check_same_thread=False without a StaticPool, so every connection gets a
*fresh* in-memory database and create_schema()'s work vanishes before the next
statement runs.
"""

import pytest
from sqlalchemy import inspect

from zephyr.db.engine import build_engine, create_schema
from zephyr.db.models import Base


def _sqlite_url(tmp_path, name="test.db"):
    return f"sqlite:///{(tmp_path / name).as_posix()}"


class TestCreateSchema:
    def test_creates_the_phase_3_tables(self, tmp_path):
        engine = build_engine(_sqlite_url(tmp_path))
        create_schema(engine)
        tables = set(inspect(engine).get_table_names())
        assert {"ai_settings", "app_state", "web_users", "guilds"} <= tables

    def test_web_users_columns(self, tmp_path):
        engine = build_engine(_sqlite_url(tmp_path))
        create_schema(engine)
        columns = {c["name"]: c for c in inspect(engine).get_columns("web_users")}
        assert set(columns) == {
            "discord_id",
            "username",
            "global_name",
            "avatar_hash",
            "refresh_token_enc",
            "token_expires_at",
            "last_login_at",
        }
        assert columns["username"]["nullable"] is False
        # Phase 3 stores no Discord tokens; the columns exist so the shape is final.
        assert columns["refresh_token_enc"]["nullable"] is True
        assert columns["token_expires_at"]["nullable"] is True

    def test_guilds_columns_are_all_optional_except_the_key(self, tmp_path):
        engine = build_engine(_sqlite_url(tmp_path))
        create_schema(engine)
        columns = {c["name"]: c for c in inspect(engine).get_columns("guilds")}
        assert set(columns) == {
            "id",
            "prefix",
            "locale",
            "timezone",
            "default_volume",
            "dj_role_id",
            "music_channel_ids",
            "enabled_cogs",
            "created_at",
        }
        assert columns["id"]["nullable"] is False
        for name in ("prefix", "locale", "timezone", "default_volume", "dj_role_id"):
            assert columns[name]["nullable"] is True, name

    def test_snowflakes_are_strings(self, tmp_path):
        """Snowflakes exceed JavaScript's safe integer range, so they are text."""
        engine = build_engine(_sqlite_url(tmp_path))
        create_schema(engine)
        web_users = {c["name"]: c for c in inspect(engine).get_columns("web_users")}
        guilds = {c["name"]: c for c in inspect(engine).get_columns("guilds")}
        assert "VARCHAR" in str(web_users["discord_id"]["type"]).upper()
        assert "VARCHAR" in str(guilds["id"]["type"]).upper()
        assert "VARCHAR" in str(guilds["dj_role_id"]["type"]).upper()

    def test_list_columns_are_json_not_array(self, tmp_path):
        """postgresql.ARRAY would break the SQLite default and every test."""
        engine = build_engine(_sqlite_url(tmp_path))
        create_schema(engine)
        guilds = {c["name"]: c for c in inspect(engine).get_columns("guilds")}
        assert "JSON" in str(guilds["music_channel_ids"]["type"]).upper()
        assert "JSON" in str(guilds["enabled_cogs"]["type"]).upper()


class TestAlembicBaseline:
    """The 0001 revision must be able to build a database from nothing."""

    def _upgrade(self, monkeypatch, url):
        from alembic import command
        from alembic.config import Config

        from zephyr.config import PROJECT_ROOT

        # env.py reads zephyr.config at exec time, so patching the module
        # attribute is enough to redirect the migration at a temporary database.
        monkeypatch.setattr("zephyr.config.DATABASE_URL", url)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.upgrade(config, "head")

    def test_upgrade_head_creates_every_table(self, tmp_path, monkeypatch):
        url = _sqlite_url(tmp_path, "alembic.db")
        self._upgrade(monkeypatch, url)
        engine = build_engine(url)
        tables = set(inspect(engine).get_table_names())
        assert {"ai_settings", "app_state", "web_users", "guilds"} <= tables

    def test_migration_matches_the_models(self, tmp_path, monkeypatch):
        """Guards the documented create_all()/Alembic drift risk."""
        url = _sqlite_url(tmp_path, "alembic.db")
        self._upgrade(monkeypatch, url)
        inspector = inspect(build_engine(url))
        for name, table in Base.metadata.tables.items():
            migrated = {c["name"] for c in inspector.get_columns(name)}
            assert migrated == set(table.columns.keys()), name


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
