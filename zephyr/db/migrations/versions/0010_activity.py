"""activity

Three tables, and the third is the one worth explaining.

``activity_daily_users`` exists because **a distinct count cannot be derived
from increments**.  "How many people spoke yesterday" is not answerable from a
per-day total, however that total is accumulated; the only way to know is to
hold a row per person per day.  The daily total is then the sum of this table's
counts, so a separate daily-rollup table would be a second copy of a number
already stored here and is deliberately absent.

``day`` is a UTC date string rather than a Date, and rather than a guild-local
day.  A guild-local day would be more meaningful and is not worth the cost: the
row would have to be written against whatever the guild's timezone was at flush
time, so changing that setting would silently re-attribute history.

``activity_totals`` is keyed on ``(guild_id, user_id)`` so the flusher can upsert
and add, which means two processes flushing the same guild cannot create two
rows for one person.

Revision ID: 0010
Revises: 0009
Create Date: 2026-09-05

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guild_activity",
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("announce_channel_id", sa.String(), nullable=True),
        sa.Column("announce_level_ups", sa.Boolean(), nullable=True),
        sa.Column("ignored_channel_ids", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("guild_id", name=op.f("pk_guild_activity")),
    )
    op.create_table(
        "activity_totals",
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False),
        sa.Column("xp", sa.Integer(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("guild_id", "user_id", name=op.f("pk_activity_totals")),
    )
    # The leaderboard's only query.
    op.create_index("ix_activity_totals_guild_id_xp", "activity_totals", ["guild_id", "xp"])
    op.create_table(
        "activity_daily_users",
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("day", sa.String(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("messages", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint(
            "guild_id", "day", "user_id", name=op.f("pk_activity_daily_users")
        ),
    )


def downgrade() -> None:
    op.drop_table("activity_daily_users")
    op.drop_index("ix_activity_totals_guild_id_xp", table_name="activity_totals")
    op.drop_table("activity_totals")
    op.drop_table("guild_activity")
