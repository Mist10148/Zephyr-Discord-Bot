"""Assign the longest ownerless legacy DM conversation to the bot owner.

DM rows written before 0007 have a NULL owner_id and are hidden from the web
history. The longest one belongs to Mist, so it is claimed for that account.
Only a still-ownerless DM row is touched.

Revision ID: 0008
Revises: 0007
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OWNER_ID = "1035442594861293598"


def upgrade() -> None:
    conn = op.get_bind()
    conversation_id = conn.execute(sa.text(
        """
        SELECT c.id
        FROM ai_conversations c
        LEFT JOIN ai_messages m ON m.conversation_id = c.id
        WHERE c.guild_id IS NULL AND c.owner_id IS NULL
        GROUP BY c.id
        ORDER BY COUNT(m.id) DESC, c.id
        LIMIT 1
        """
    )).scalar()
    if conversation_id is None:
        return
    conn.execute(
        sa.text("UPDATE ai_conversations SET owner_id = :owner WHERE id = :id AND owner_id IS NULL"),
        {"owner": OWNER_ID, "id": conversation_id},
    )


def downgrade() -> None:
    # Ownership is data, not schema; leave the claim in place.
    pass
