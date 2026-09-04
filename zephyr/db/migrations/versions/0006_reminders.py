"""reminders

Due-ness is a SQL predicate here rather than a Python comprehension, which is
why ``ix_reminders_due_at`` exists: the claim runs
``WHERE due_at <= :now AND fired_at IS NULL`` on every tick, and the row count
is unbounded.  ``weather_subs`` can afford to filter in Python because a wall
clock intent depends on the row's own DST state and the set is bounded by
MAX_SUBS_PER_GUILD; a reminder is an instant and there is no such bound.

``due_at`` is always UTC.  ``tz`` is stored alongside it so a confirmation can
be rendered in the zone the person typed in, and so a repeating reminder can
stay at the same *local* time across a DST change.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-05

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "reminders",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.String(), nullable=False),
        # NULL when created in a DM: there is no guild, and delivery is a DM.
        sa.Column("guild_id", sa.String(), nullable=True),
        sa.Column("channel_id", sa.String(), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tz", sa.String(), nullable=False),
        # NULL for a one-shot.
        sa.Column("repeat_every_seconds", sa.Integer(), nullable=True),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminders")),
    )
    op.create_index("ix_reminders_due_at", "reminders", ["due_at"], unique=False)
    op.create_index("ix_reminders_user_id_due_at", "reminders", ["user_id", "due_at"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_reminders_user_id_due_at", table_name="reminders")
    op.drop_index("ix_reminders_due_at", table_name="reminders")
    op.drop_table("reminders")
