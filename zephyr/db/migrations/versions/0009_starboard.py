"""starboard

Two tables, because the two have opposite read patterns.  ``guild_starboards``
is consulted on *every reaction* in a configured guild (through a cache) and
changes almost never; ``starboard_entries`` is written only for the reactions
that actually cross the threshold.

The unique constraint on ``(guild_id, source_message_id)`` is the load-bearing
part of this migration.  Reactions arrive as independent gateway events with no
ordering guarantee, so two arriving close together both read "not promoted yet"
and both try to post.  The constraint is what makes the listener idempotent: the
second insert is refused, and the handler treats that as "already promoted"
rather than posting a duplicate.

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-05

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "guild_starboards",
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=True),
        sa.Column("channel_id", sa.String(), nullable=True),
        sa.Column("threshold", sa.Integer(), nullable=True),
        sa.Column("emoji", sa.String(), nullable=True),
        sa.Column("allow_self_star", sa.Boolean(), nullable=True),
        sa.Column("ignored_channel_ids", sa.JSON(), nullable=True),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("guild_id", name=op.f("pk_guild_starboards")),
    )
    op.create_table(
        "starboard_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("source_channel_id", sa.String(), nullable=False),
        sa.Column("source_message_id", sa.String(), nullable=False),
        # Nullable for the window between claiming the row and the post
        # succeeding: a row with no message id is a failed promotion the next
        # reaction retries, which beats both leaving no row (and double-posting)
        # and committing an id that does not exist.
        sa.Column("starboard_message_id", sa.String(), nullable=True),
        sa.Column("star_count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_starboard_entries")),
        sa.UniqueConstraint(
            "guild_id", "source_message_id",
            name=op.f("uq_starboard_entries_guild_id_source_message_id"),
        ),
    )


def downgrade() -> None:
    op.drop_table("starboard_entries")
    op.drop_table("guild_starboards")
