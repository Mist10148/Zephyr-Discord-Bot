"""initial schema: ai_settings, app_state, web_users, guilds

Baseline for Zephyr's database.  It covers the two Phase 0 tables as well as the
two added in Phase 3, so a fresh database can be built by migration alone and
``alembic downgrade base`` is meaningful.

For the already-deployed database -- where ai_settings and app_state exist
because create_all() made them -- run ``alembic stamp 0001`` instead of upgrading.
See docs/DEPLOYMENT.md.

Revision ID: 0001
Revises:
Create Date: 2026-07-30

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ai_settings",
        sa.Column("context_key", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("context_key", name=op.f("pk_ai_settings")),
    )
    op.create_table(
        "app_state",
        sa.Column("key", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_app_state")),
    )
    op.create_table(
        "web_users",
        sa.Column("discord_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("global_name", sa.String(), nullable=True),
        sa.Column("avatar_hash", sa.String(), nullable=True),
        sa.Column("refresh_token_enc", sa.String(), nullable=True),
        sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_login_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("discord_id", name=op.f("pk_web_users")),
    )
    op.create_table(
        "guilds",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=True),
        sa.Column("locale", sa.String(), nullable=True),
        sa.Column("timezone", sa.String(), nullable=True),
        sa.Column("default_volume", sa.Integer(), nullable=True),
        sa.Column("dj_role_id", sa.String(), nullable=True),
        sa.Column("music_channel_ids", sa.JSON(), nullable=True),
        sa.Column("enabled_cogs", sa.JSON(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_guilds")),
    )


def downgrade() -> None:
    op.drop_table("guilds")
    op.drop_table("web_users")
    op.drop_table("app_state")
    op.drop_table("ai_settings")
