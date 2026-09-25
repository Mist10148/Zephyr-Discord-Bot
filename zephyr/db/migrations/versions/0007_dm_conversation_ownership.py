"""Persist DM conversation ownership.

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.add_column(sa.Column("owner_id", sa.String(), nullable=True))
    op.create_index(
        "ix_ai_conversations_owner_id_updated_at",
        "ai_conversations",
        ["owner_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ai_conversations_owner_id_updated_at", table_name="ai_conversations")
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.drop_column("owner_id")