"""SQLAlchemy models for persisted Zephyr settings."""

from datetime import datetime

from sqlalchemy import JSON, DateTime, Integer, MetaData, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.sql import func


NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class AISettings(Base):
    __tablename__ = "ai_settings"

    context_key: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AppState(Base):
    __tablename__ = "app_state"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    data: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# Discord snowflakes are stored as String, not BigInteger: they exceed
# JavaScript's safe integer range, so the JSON API has to emit them as strings
# anyway.  Storing strings removes an int<->str conversion at every boundary and
# matches ai_settings.context_key.  List columns use JSON rather than
# postgresql.ARRAY because the default database is SQLite (see config.py).


class WebUser(Base):
    """A Discord account that has signed in to the dashboard.

    A login-audit row, not the authorization source -- that is the session.  The
    token columns exist so the table shape is final, but Phase 3 stores no
    Discord tokens at all and leaves both NULL.
    """

    __tablename__ = "web_users"

    discord_id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(String, nullable=False)
    global_name: Mapped[str | None] = mapped_column(String, nullable=True)
    avatar_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token_enc: Mapped[str | None] = mapped_column(String, nullable=True)
    token_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class Guild(Base):
    """Per-guild dashboard settings.

    A row appears only once a guild has been configured, so its absence is not a
    statement about bot membership -- that comes from the zephyr:guilds snapshot.
    Phase 3 reads this table and never writes it.
    """

    __tablename__ = "guilds"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    prefix: Mapped[str | None] = mapped_column(String, nullable=True)
    locale: Mapped[str | None] = mapped_column(String, nullable=True)
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    default_volume: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dj_role_id: Mapped[str | None] = mapped_column(String, nullable=True)
    music_channel_ids: Mapped[list | None] = mapped_column(JSON, nullable=True)
    enabled_cogs: Mapped[list | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
