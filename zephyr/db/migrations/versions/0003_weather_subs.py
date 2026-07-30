"""weather_subs and bot_users

Phase 5's schema.  schedule_local_time is text, not Time: it stores a wall-clock
intent ("08:00 in Manila"), which across a DST change is deliberately a different
UTC instant.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-30

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "weather_subs",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("location", sa.String(), nullable=False),
        sa.Column("lat", sa.Float(), nullable=False),
        sa.Column("lon", sa.Float(), nullable=False),
        sa.Column("units", sa.String(), nullable=False),
        sa.Column("schedule_local_time", sa.String(), nullable=True),
        sa.Column("tz", sa.String(), nullable=False),
        sa.Column("thresholds", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_fingerprint", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_weather_subs")),
    )
    op.create_index("ix_weather_subs_guild_id", "weather_subs", ["guild_id"])
    op.create_table(
        "bot_users",
        sa.Column("discord_id", sa.String(), nullable=False),
        sa.Column("default_city", sa.String(), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lon", sa.Float(), nullable=True),
        sa.Column("units", sa.String(), nullable=False),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("discord_id", name=op.f("pk_bot_users")),
    )


def downgrade() -> None:
    op.drop_table("bot_users")
    op.drop_index("ix_weather_subs_guild_id", table_name="weather_subs")
    op.drop_table("weather_subs")
