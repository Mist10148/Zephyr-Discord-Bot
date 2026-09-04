"""tags

``UniqueConstraint(guild_id, name)`` is the load-bearing part.  Without it
``/tag-create`` would need a read-then-write, and two people creating the same
tag at once would both read "no such tag" -- leaving two rows where the lookup
returns whichever one the query planner reached first.

``name`` is stored already normalised (lowercased and trimmed), so the
constraint means what a person means by "the same tag".  Storing the raw text
and comparing case-insensitively at read time would make ``Rules`` and ``rules``
two rows that both answer to ``/tag rules``.

No index beyond the constraint: the constraint indexes ``(guild_id, name)``,
which is the lookup, and ``/tag-list`` reads one guild's tags through the same
index prefix.

Revision ID: 0011
Revises: 0010
Create Date: 2026-09-05

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("guild_id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column("uses", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tags")),
        sa.UniqueConstraint("guild_id", "name", name=op.f("uq_tags_guild_id_name")),
    )


def downgrade() -> None:
    op.drop_table("tags")
