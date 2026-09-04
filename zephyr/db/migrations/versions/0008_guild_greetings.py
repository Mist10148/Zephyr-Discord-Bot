"""guild_greetings

A table rather than more columns on ``guilds``.  Seven columns that matter to
one feature would widen a row that is read on every settings page and by three
bulk readers on hot paths -- and a greeting is the only setting here with a
*body*, so folding a Text column into a row of short scalars would make every
one of those reads carry it.

``guild_id`` is the primary key: exactly one row per guild, so the upsert needs
no uniqueness reasoning of its own.

The flags are nullable rather than NOT NULL DEFAULT false, matching 0005's
reasoning: SQLite cannot add a NOT NULL column without a default, and a
three-state column for a two-state setting is a cost paid once in the reader
rather than forever in the migration chain.

Revision ID: 0008
Revises: 0007
Create Date: 2026-09-05

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guild_greetings",
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("welcome_enabled", sa.Boolean(), nullable=True),
        sa.Column("welcome_channel_id", sa.String(), nullable=True),
        sa.Column("welcome_message", sa.Text(), nullable=True),
        sa.Column("farewell_enabled", sa.Boolean(), nullable=True),
        sa.Column("farewell_channel_id", sa.String(), nullable=True),
        sa.Column("farewell_message", sa.Text(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("guild_id", name=op.f("pk_guild_greetings")),
    )


def downgrade() -> None:
    op.drop_table("guild_greetings")
