"""Welcome and farewell configuration.

Small on purpose: an upsert, a single read, and a bulk read for the cog's cache.
The bulk read exists for the same reason ``read_dj_roles`` does -- the listener
fires on every join and every leave across every guild, and a database round
trip on that path would be paid constantly to answer a question that changes
roughly never.
"""

from __future__ import annotations

from sqlalchemy import delete, select

from zephyr.db.models import GuildGreeting
from zephyr.db.session import get_engine

WRITABLE_COLUMNS = (
    "welcome_enabled",
    "welcome_channel_id",
    "welcome_message",
    "farewell_enabled",
    "farewell_channel_id",
    "farewell_message",
)

_COLUMNS = (
    GuildGreeting.guild_id,
    GuildGreeting.welcome_enabled,
    GuildGreeting.welcome_channel_id,
    GuildGreeting.welcome_message,
    GuildGreeting.farewell_enabled,
    GuildGreeting.farewell_channel_id,
    GuildGreeting.farewell_message,
)


def read(guild_id: str, *, database_url: str | None = None) -> dict | None:
    """One guild's greetings, or None when it has never configured any."""
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_COLUMNS).where(GuildGreeting.guild_id == str(guild_id))
        ).mappings().first()
    return _normalise(row) if row else None


def write(guild_id: str, values: dict, *, database_url: str | None = None) -> dict:
    """Create or update one guild's greetings and return the stored row.

    ON CONFLICT DO UPDATE, and only the keys present in ``values``, so setting
    the farewell channel cannot blank the welcome message by omission -- the
    same contract ``write_guild_settings`` offers.
    """
    filtered = {key: value for key, value in values.items() if key in WRITABLE_COLUMNS}
    engine = get_engine(database_url)
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert

        statement = insert(GuildGreeting.__table__).values(guild_id=str(guild_id), **filtered)
        if filtered:
            statement = statement.on_conflict_do_update(
                index_elements=["guild_id"],
                set_={key: statement.excluded[key] for key in filtered},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=["guild_id"])
        connection.execute(statement)
    return read(guild_id, database_url=database_url) or {}


def read_all(*, database_url: str | None = None) -> dict[str, dict]:
    """Every configured guild's greetings, keyed by guild id.

    Only guilds that have enabled one or the other: the listener consults this
    on every join and leave, so the cache should hold the guilds that actually
    want a greeting rather than every guild that ever opened the settings page.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(*_COLUMNS).where(
                GuildGreeting.welcome_enabled.is_(True)
                | GuildGreeting.farewell_enabled.is_(True)
            )
        ).mappings().all()
    return {str(row["guild_id"]): _normalise(row) for row in rows}


def delete_for_guild(guild_id: str, *, database_url: str | None = None) -> bool:
    """Forget a guild's greetings entirely, for when the bot is removed."""
    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(
            delete(GuildGreeting).where(GuildGreeting.guild_id == str(guild_id))
        )
    return bool(result.rowcount)


def _normalise(row) -> dict:
    """NULL flags read as False.

    The columns are nullable because 0008 follows 0005's pattern, and a
    three-state value for a two-state setting would otherwise have to be handled
    by every consumer -- including a template renderer that has no business
    knowing about migration constraints.
    """
    data = dict(row)
    for key in ("welcome_enabled", "farewell_enabled"):
        data[key] = bool(data.get(key))
    return data
