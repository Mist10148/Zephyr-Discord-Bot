"""additive columns for the Phase 13-15 features

One revision for eleven columns across four tables, rather than one revision per
feature branch.  Those branches are developed in parallel and each would
otherwise name itself 0005, forking the down_revision chain -- and the fork is
hard to attribute after the fact, because models.py merges cleanly (each branch
merely appended a column) while the migration graph does not.  Batching removes
the shared file from every feature branch instead: after this revision, none of
them touches Guild, BotUser, WeatherSub or AIMessage at all.

Every column is nullable with no server_default.  That is required twice over.
SQLite's native ALTER TABLE ADD COLUMN cannot add NOT NULL without a default, so
plain op.add_column works on both engines and no batch_alter_table is needed on
the way up; and on Postgres 11+ an ADD COLUMN with no default is metadata-only,
so this locks nothing on a live database.  It also matches what
website/api/guilds.py expects: it substitutes DEFAULT_SETTINGS for a NULL and
reports which keys it filled in, a distinction a server_default would erase.

The way down does need batch_alter_table -- SQLite implements DROP COLUMN by
rebuilding the table.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-04

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 13.3 -- per-guild text-to-speech language.
    op.add_column("guilds", sa.Column("tts_language", sa.String(), nullable=True))
    # 14.4 -- where the AI answers a mention.
    op.add_column("guilds", sa.Column("ai_channel_mode", sa.String(), nullable=True))
    op.add_column("guilds", sa.Column("ai_channel_ids", sa.JSON(), nullable=True))
    # 15.3 -- where moderation cases are posted.
    op.add_column("guilds", sa.Column("modlog_channel_id", sa.String(), nullable=True))
    # 15.4 -- music governance.
    op.add_column("guilds", sa.Column("dj_only", sa.Boolean(), nullable=True))
    op.add_column("guilds", sa.Column("always_on", sa.Boolean(), nullable=True))
    op.add_column("guilds", sa.Column("always_on_channel_id", sa.String(), nullable=True))
    op.add_column("guilds", sa.Column("vote_skip_ratio", sa.Integer(), nullable=True))

    # 14.4 -- a per-user daily Gemini ceiling.
    op.add_column("bot_users", sa.Column("ai_token_budget", sa.Integer(), nullable=True))

    # 14.5 -- snooze, as distinct from disable.
    op.add_column(
        "weather_subs", sa.Column("muted_until", sa.DateTime(timezone=True), nullable=True)
    )

    # 15.2 -- attribute a transcript line to its author so it can be erased.
    op.add_column("ai_messages", sa.Column("author_id", sa.String(), nullable=True))
    op.create_index("ix_ai_messages_author_id", "ai_messages", ["author_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_ai_messages_author_id", table_name="ai_messages")
    with op.batch_alter_table("ai_messages") as batch:
        batch.drop_column("author_id")

    with op.batch_alter_table("weather_subs") as batch:
        batch.drop_column("muted_until")

    with op.batch_alter_table("bot_users") as batch:
        batch.drop_column("ai_token_budget")

    with op.batch_alter_table("guilds") as batch:
        batch.drop_column("vote_skip_ratio")
        batch.drop_column("always_on_channel_id")
        batch.drop_column("always_on")
        batch.drop_column("dj_only")
        batch.drop_column("modlog_channel_id")
        batch.drop_column("ai_channel_ids")
        batch.drop_column("ai_channel_mode")
        batch.drop_column("tts_language")
