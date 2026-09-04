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
            "tts_language",
            "ai_channel_mode",
            "ai_channel_ids",
            "modlog_channel_id",
            "dj_only",
            "always_on",
            "always_on_channel_id",
            "vote_skip_ratio",
            "created_at",
        }
        assert columns["id"]["nullable"] is False
        # Everything but the key and created_at, which is a fact about the row
        # rather than a setting.  A configurable column that is NOT NULL would
        # make "never configured" indistinguishable from "set to the default".
        for name in set(columns) - {"id", "created_at"}:
            assert columns[name]["nullable"] is True, name
            assert columns[name]["default"] is None, name

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


class TestAutoCreateIsSqliteOnly:
    """create_all() builds SQLite; Alembic owns a configured server database.

    Note the monkeypatch target. ``zephyr/db/engine.py`` does
    ``from zephyr.config import DB_AUTO_CREATE``, which binds the value at
    import, so patching ``zephyr.config`` would have no effect -- the same
    reason ``tests/test_ai_memory_reset.py`` patches ``session.DATABASE_URL``
    rather than the config module.
    """

    def test_sqlite_creates_itself(self):
        from zephyr.db.engine import should_auto_create

        assert should_auto_create("sqlite:///data/zephyr.db") is True

    def test_a_server_database_is_left_to_alembic(self):
        from zephyr.db.engine import should_auto_create

        assert should_auto_create("postgresql+psycopg://u:p@host/db") is False
        # The bare postgres:// form Render hands out is normalised first, so the
        # decision must survive the rewrite rather than pattern-match the input.
        assert should_auto_create("postgres://u:p@host/db") is False

    def test_the_env_var_overrides_in_both_directions(self, monkeypatch):
        from zephyr.db import engine

        monkeypatch.setattr(engine, "DB_AUTO_CREATE", True)
        assert engine.should_auto_create("postgresql+psycopg://u:p@host/db") is True
        monkeypatch.setattr(engine, "DB_AUTO_CREATE", False)
        assert engine.should_auto_create("sqlite:///data/zephyr.db") is False


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

    def test_migrations_and_create_all_agree(self, tmp_path, monkeypatch):
        """The real drift check: both schema paths must produce the same database.

        Development and container startup use create_all(); an existing deployment is
        upgraded with Alembic. If the two disagree, a bug appears only in production
        (or only locally), which is the worst possible split. Compares tables *and*
        per-table column sets in both directions.
        """
        migrated_url = _sqlite_url(tmp_path, "migrated.db")
        self._upgrade(monkeypatch, migrated_url)
        migrated = inspect(build_engine(migrated_url))

        created_engine = build_engine(_sqlite_url(tmp_path, "created.db"))
        create_schema(created_engine)
        created = inspect(created_engine)

        # alembic_version is Alembic's own bookkeeping and has no model behind it.
        migrated_tables = set(migrated.get_table_names()) - {"alembic_version"}
        assert migrated_tables == set(created.get_table_names())
        for table in migrated_tables:
            assert ({c["name"] for c in migrated.get_columns(table)}
                    == {c["name"] for c in created.get_columns(table)}), table

    def test_downgrade_to_base_is_reversible(self, tmp_path, monkeypatch):
        """`alembic downgrade base` has to actually work, or the baseline is a fiction."""
        from alembic import command
        from alembic.config import Config

        from zephyr.config import PROJECT_ROOT

        url = _sqlite_url(tmp_path, "alembic.db")
        self._upgrade(monkeypatch, url)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        command.downgrade(config, "base")

        remaining = set(inspect(build_engine(url)).get_table_names())
        assert not ({"ai_settings", "app_state", "web_users", "guilds"} & remaining)

    def test_the_chain_has_exactly_one_head(self):
        """A forked revision graph must fail here, before anyone runs a migration.

        The chain is linear by convention -- bare zero-padded revision ids, no
        branch_labels, no depends_on -- and parallel feature branches are the way
        it stops being linear: two branches both claim the next number, and
        Alembic ends up with two heads. That is cheap to fix at the moment it
        happens (rename a file, edit two module variables) and expensive
        afterwards, because `upgrade head` then refuses to run at all and the
        error names a revision rather than a branch.

        Resolving two heads with an Alembic *merge* revision is prohibited: it
        would make test_every_revision_downgrades_one_step meaningless.
        """
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from zephyr.config import PROJECT_ROOT

        heads = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini"))).get_heads()
        assert len(heads) == 1, f"the revision chain has forked: {heads}"

    def test_every_revision_downgrades_one_step(self, tmp_path, monkeypatch):
        """Each downgrade() must reverse its own upgrade(), not just the aggregate.

        test_downgrade_to_base_is_reversible proves the whole chain unwinds, which
        was enough at four revisions. It stops being enough as the chain grows: a
        single broken downgrade() in the middle still fails that test, but the
        failure names the last step attempted rather than the guilty revision.
        Walking down one revision at a time makes it attributable.
        """
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        from zephyr.config import PROJECT_ROOT

        url = _sqlite_url(tmp_path, "stepwise.db")
        self._upgrade(monkeypatch, url)
        config = Config(str(PROJECT_ROOT / "alembic.ini"))
        script = ScriptDirectory.from_config(config)

        for revision in script.walk_revisions():
            # Attribute a failure to the revision whose downgrade() ran, which
            # is what the bare exception from `downgrade base` does not tell you.
            try:
                command.downgrade(config, "-1")
            except Exception as exc:  # pragma: no cover - only on a broken revision
                raise AssertionError(
                    f"revision {revision.revision} does not downgrade one step: {exc}"
                ) from exc

        remaining = set(inspect(build_engine(url)).get_table_names())
        assert remaining <= {"alembic_version"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
