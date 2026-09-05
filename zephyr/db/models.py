"""SQLAlchemy models for persisted Zephyr settings."""

from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
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


class Playlist(Base):
    """A saved queue, owned by the user who saved it.

    ``guild_id`` records where it was saved rather than restricting where it can
    be loaded: a playlist is the user's, and the common case -- saving in one
    server and loading in another -- must not require a copy.  It is nullable so a
    playlist created from the dashboard's own editor, which has no guild context,
    is not forced to invent one.

    ``(owner_id, name)`` is unique so ``/save weekend`` twice replaces rather than
    accumulating indistinguishable duplicates, and so that uniqueness is enforced
    by the database instead of by a read-then-write two clients can interleave.
    Its index doubles as the one every "my playlists" query needs.
    """

    __tablename__ = "playlists"
    # Unnamed on purpose: Base's naming convention renders it uq_playlists_owner_id,
    # which is what Alembic's autogenerate will also produce.
    __table_args__ = (UniqueConstraint("owner_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    owner_id: Mapped[str] = mapped_column(String, nullable=False)
    guild_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class PlaylistTrack(Base):
    """One entry of a saved queue: the serializable half of ``cogs.music.Track``.

    ``url`` is nullable, which is the whole point.  A Spotify import stores a
    title and nothing else, and ``YTDLSource.from_track`` resolves it by title at
    play time -- so importing 200 tracks costs two Spotify calls instead of 200
    yt-dlp extractions, and a saved playlist keeps working after the video it was
    saved from is taken down.

    The primary key is (playlist_id, position), so ordering is the identity of a
    row rather than a sortable attribute of one.  Reordering therefore rewrites
    the whole list in one transaction; see ``zephyr/db/playlists.py``.
    """

    __tablename__ = "playlist_tracks"

    playlist_id: Mapped[int] = mapped_column(
        ForeignKey("playlists.id", ondelete="CASCADE"), primary_key=True
    )
    position: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String, nullable=False)
    url: Mapped[str | None] = mapped_column(String, nullable=True)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String, nullable=False, default="youtube")


class AuditLog(Base):
    """Who changed what, from where.

    Deferred in Phase 3 because nothing mutated yet; ``PATCH /guilds/<id>/settings``
    and the player bridge are the writers that make it real.  ``source`` separates
    'web' from 'discord' so a dashboard change and a slash command are
    distinguishable after the fact.

    Reading it back is a Phase 7 concern, but the index it will need is cheap now
    and painful to add to a populated table later.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_guild_id_created_at", "guild_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str | None] = mapped_column(String, nullable=True)
    actor_id: Mapped[str] = mapped_column(String, nullable=False)
    action: Mapped[str] = mapped_column(String, nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    source: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class AIConversation(Base):
    """Persisted context for one Discord channel.

    Only exchanges directed at Zephyr are inserted.  ``guild_id`` is nullable so
    DMs can use the same machinery, while the dashboard can safely scope reads
    to a server it has authorized.
    """

    __tablename__ = "ai_conversations"
    __table_args__ = (Index("ix_ai_conversations_guild_id_updated_at", "guild_id", "updated_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    guild_id: Mapped[str | None] = mapped_column(String, nullable=True)
    rolling_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class AIMessage(Base):
    __tablename__ = "ai_messages"
    __table_args__ = (Index("ix_ai_messages_conversation_id_created_at", "conversation_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("ai_conversations.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Persona(Base):
    __tablename__ = "personas"
    __table_args__ = (UniqueConstraint("guild_id", "name"), Index("ix_personas_guild_id", "guild_id"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    system_prompt: Mapped[str] = mapped_column(Text, nullable=False)
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class WeatherSub(Base):
    """A standing request for weather to arrive without being asked.

    Three kinds, deliberately in one table rather than three: they share a
    channel, a location, an enabled flag and a runner, and differ only in when
    they fire.

    * ``daily``            -- a digest at ``schedule_local_time`` in ``tz``.
    * ``severe``           -- posted when ``thresholds`` are crossed.
    * ``class_suspension`` -- posted when the heat index reaches an advisory level.

    ``lat``/``lon`` are resolved once, at subscription time, and stored.  A
    scheduler that geocoded on every run would make an extra network call per
    subscription per tick, and would silently start posting about a different
    place if the geocoder ever changed its mind about the name.  ``location`` is
    kept alongside them for display.
    """

    __tablename__ = "weather_subs"
    __table_args__ = (Index("ix_weather_subs_guild_id", "guild_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    guild_id: Mapped[str] = mapped_column(String, nullable=False)
    channel_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    location: Mapped[str] = mapped_column(String, nullable=False)
    lat: Mapped[float] = mapped_column(Float, nullable=False)
    lon: Mapped[float] = mapped_column(Float, nullable=False)
    units: Mapped[str] = mapped_column(String, nullable=False, default="metric")
    # "HH:MM" local wall-clock time, and the zone it is local to.  Stored as text
    # rather than a Time column because it is a wall-clock intent ("08:00 in
    # Manila"), not an instant -- across a DST change the same string is a
    # different UTC moment, which is exactly the desired behaviour.
    schedule_local_time: Mapped[str | None] = mapped_column(String, nullable=True)
    tz: Mapped[str] = mapped_column(String, nullable=False, default="UTC")
    thresholds: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # What was last posted, hashed.  A severe watcher runs every 15 minutes and
    # the same storm is still there on the next tick; without this the channel
    # would receive the same warning four times an hour until the weather changed.
    last_fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class BotUser(Base):
    """A Discord user's own weather defaults, set with /setlocation.

    Separate from ``web_users``: that table records dashboard sign-ins, and most
    people who set a default city will never open the dashboard at all.
    """

    __tablename__ = "bot_users"

    discord_id: Mapped[str] = mapped_column(String, primary_key=True)
    default_city: Mapped[str | None] = mapped_column(String, nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    units: Mapped[str] = mapped_column(String, nullable=False, default="metric")
    timezone: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
