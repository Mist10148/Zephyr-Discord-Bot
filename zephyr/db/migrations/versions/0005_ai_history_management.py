"""AI history management metadata and message revisions.

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.add_column(sa.Column("category", sa.String(), nullable=True))
        batch_op.add_column(
            sa.Column("is_archived", sa.Boolean(), nullable=False, server_default=sa.false())
        )

    with op.batch_alter_table("ai_messages") as batch_op:
        batch_op.add_column(
            sa.Column("version", sa.Integer(), nullable=False, server_default="1")
        )
        batch_op.add_column(sa.Column("edited_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("redacted_at", sa.DateTime(timezone=True), nullable=True))

    op.create_table(
        "ai_message_revisions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("message_id", sa.Integer(), nullable=False),
        sa.Column("editor_id", sa.String(), nullable=False),
        sa.Column("previous_content", sa.Text(), nullable=False),
        sa.Column("replacement_content", sa.Text(), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["message_id"], ["ai_messages.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_ai_message_revisions_message_id_created_at",
        "ai_message_revisions",
        ["message_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ai_message_revisions_message_id_created_at",
        table_name="ai_message_revisions",
    )
    op.drop_table("ai_message_revisions")
    with op.batch_alter_table("ai_messages") as batch_op:
        batch_op.drop_column("redacted_at")
        batch_op.drop_column("edited_at")
        batch_op.drop_column("version")
    with op.batch_alter_table("ai_conversations") as batch_op:
        batch_op.drop_column("is_archived")
        batch_op.drop_column("category")