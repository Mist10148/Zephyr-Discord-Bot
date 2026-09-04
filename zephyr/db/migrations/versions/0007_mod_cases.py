"""mod_cases

Moderation cases, numbered per guild.  ``case_number`` duplicates the primary
key deliberately: a moderator says "case 12", and a global id would both read
absurdly and leak how busy every other server is.

The unique constraint on ``(guild_id, case_number)`` is not decoration -- it is
the allocation guard.  The number is chosen as ``MAX(case_number) + 1`` inside
the inserting transaction, so two moderators acting in the same second both read
the same maximum; the constraint rejects the loser, which retries.  Without it
one of the two actions would silently overwrite the other's number and the
history would have a hole in it.

No separate index on those two columns: the constraint already provides one, and
a second would be dead weight on every insert.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-05

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mod_cases",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("case_number", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("target_id", sa.String(), nullable=False),
        # The display name as it was: a banned account cannot be resolved from
        # the gateway afterwards.
        sa.Column("target_tag", sa.String(), nullable=True),
        sa.Column("moderator_id", sa.String(), nullable=False),
        # NULL means no reason was given, which is different from "" -- /reason
        # exists to fill exactly this in afterwards.
        sa.Column("reason", sa.Text(), nullable=True),
        # Only a timeout has one.
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mod_cases")),
        sa.UniqueConstraint(
            "guild_id", "case_number", name=op.f("uq_mod_cases_guild_id_case_number")
        ),
    )
    op.create_index(
        "ix_mod_cases_guild_id_target_id", "mod_cases", ["guild_id", "target_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_mod_cases_guild_id_target_id", table_name="mod_cases")
    op.drop_table("mod_cases")
