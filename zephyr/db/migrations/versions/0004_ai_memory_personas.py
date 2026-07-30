"""Phase 6 AI memory and personas.

Revision ID: 0004
Revises: 0003
"""
from collections.abc import Sequence
from alembic import op
import sqlalchemy as sa

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    op.create_table("ai_conversations", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("channel_id", sa.String(), nullable=False, unique=True), sa.Column("guild_id", sa.String(), nullable=True), sa.Column("rolling_summary", sa.Text(), nullable=True), sa.Column("token_count", sa.Integer(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ai_conversations_guild_id_updated_at", "ai_conversations", ["guild_id", "updated_at"])
    op.create_table("ai_messages", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("conversation_id", sa.Integer(), sa.ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False), sa.Column("role", sa.String(), nullable=False), sa.Column("content", sa.Text(), nullable=False), sa.Column("tokens", sa.Integer(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    op.create_index("ix_ai_messages_conversation_id_created_at", "ai_messages", ["conversation_id", "created_at"])
    op.create_table("personas", sa.Column("id", sa.Integer(), primary_key=True), sa.Column("guild_id", sa.String(), nullable=False), sa.Column("name", sa.String(), nullable=False), sa.Column("system_prompt", sa.Text(), nullable=False), sa.Column("is_default", sa.Boolean(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False), sa.UniqueConstraint("guild_id", "name", name=op.f("uq_personas_guild_id")))
    op.create_index("ix_personas_guild_id", "personas", ["guild_id"])

def downgrade() -> None:
    op.drop_index("ix_personas_guild_id", table_name="personas"); op.drop_table("personas")
    op.drop_index("ix_ai_messages_conversation_id_created_at", table_name="ai_messages"); op.drop_table("ai_messages")
    op.drop_index("ix_ai_conversations_guild_id_updated_at", table_name="ai_conversations"); op.drop_table("ai_conversations")
