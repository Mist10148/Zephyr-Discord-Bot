"""Starboard configuration and promoted-message storage.

The interesting function is ``claim``, and the interesting part of it is that it
does not check whether a message is already promoted -- it *tries to insert* and
lets the unique constraint answer.

Reactions arrive as independent gateway events with no ordering guarantee. A
read-then-insert therefore races with itself: two reactions landing close
together both read "not promoted yet", both insert, and the message appears in
the starboard twice with the second row orphaning the first. Insert-and-catch
has no window, because the constraint is evaluated by the database rather than
by us.
"""

from __future__ import annotations

from sqlalchemy import delete, insert, select, update
from sqlalchemy.exc import IntegrityError

from zephyr.db.models import GuildStarboard, StarboardEntry
from zephyr.db.session import get_engine

# The defaults a guild gets before it configures anything. Five is high enough
# that a couple of friends cannot promote each other's messages and low enough
# to work in a small server.
DEFAULT_THRESHOLD = 5
DEFAULT_EMOJI = "⭐"
MIN_THRESHOLD = 1
MAX_THRESHOLD = 100

CONFIG_COLUMNS = (
    "enabled",
    "channel_id",
    "threshold",
    "emoji",
    "allow_self_star",
    "ignored_channel_ids",
)

_CONFIG = (
    GuildStarboard.guild_id,
    GuildStarboard.enabled,
    GuildStarboard.channel_id,
    GuildStarboard.threshold,
    GuildStarboard.emoji,
    GuildStarboard.allow_self_star,
    GuildStarboard.ignored_channel_ids,
)

_ENTRY = (
    StarboardEntry.id,
    StarboardEntry.guild_id,
    StarboardEntry.source_channel_id,
    StarboardEntry.source_message_id,
    StarboardEntry.starboard_message_id,
    StarboardEntry.star_count,
)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def read_config(guild_id: str, *, database_url: str | None = None) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_CONFIG).where(GuildStarboard.guild_id == str(guild_id))
        ).mappings().first()
    return _normalise(row) if row else None


def write_config(guild_id: str, values: dict, *, database_url: str | None = None) -> dict:
    filtered = {key: value for key, value in values.items() if key in CONFIG_COLUMNS}
    engine = get_engine(database_url)
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as upsert
        else:
            from sqlalchemy.dialects.sqlite import insert as upsert

        statement = upsert(GuildStarboard.__table__).values(guild_id=str(guild_id), **filtered)
        if filtered:
            statement = statement.on_conflict_do_update(
                index_elements=["guild_id"],
                set_={key: statement.excluded[key] for key in filtered},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=["guild_id"])
        connection.execute(statement)
    return read_config(guild_id, database_url=database_url) or {}


def read_all_configs(*, database_url: str | None = None) -> dict[str, dict]:
    """Every enabled starboard, keyed by guild id.

    Only the enabled ones: this backs the cache the reaction listener consults,
    and the listener runs on every reaction in every guild the bot is in. A
    cache holding every guild that ever opened the settings page would make the
    cheapest guard in the listener the largest dictionary lookup.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(*_CONFIG).where(GuildStarboard.enabled.is_(True))
        ).mappings().all()
    return {str(row["guild_id"]): _normalise(row) for row in rows}


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------


def get_entry(
    guild_id: str, source_message_id: str, *, database_url: str | None = None
) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_ENTRY).where(
                StarboardEntry.guild_id == str(guild_id),
                StarboardEntry.source_message_id == str(source_message_id),
            )
        ).mappings().first()
    return dict(row) if row else None


def claim(
    *,
    guild_id: str,
    source_channel_id: str,
    source_message_id: str,
    star_count: int,
    database_url: str | None = None,
) -> dict | None:
    """Reserve the right to post this message to the starboard.

    Returns the new row on success, or ``None`` when somebody else already
    claimed it -- which the caller reads as "already promoted, just update the
    count".

    Insert-and-catch rather than read-then-insert. See the module docstring: a
    read-then-insert races with itself because reactions are independent gateway
    events, and the failure is a message posted to the starboard twice.
    """
    engine = get_engine(database_url)
    try:
        with engine.begin() as connection:
            entry_id = connection.execute(
                insert(StarboardEntry).values(
                    guild_id=str(guild_id),
                    source_channel_id=str(source_channel_id),
                    source_message_id=str(source_message_id),
                    starboard_message_id=None,
                    star_count=int(star_count),
                )
            ).inserted_primary_key[0]
    except IntegrityError:
        return None
    return get_entry(guild_id, source_message_id, database_url=database_url)


def attach_message(
    guild_id: str,
    source_message_id: str,
    starboard_message_id: str,
    *,
    database_url: str | None = None,
) -> None:
    """Record which starboard message represents this source message.

    Written after the post succeeds rather than as part of the claim, so a
    failed post leaves a row with a NULL id that the next reaction retries --
    rather than an id pointing at a message that was never created.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            update(StarboardEntry)
            .where(
                StarboardEntry.guild_id == str(guild_id),
                StarboardEntry.source_message_id == str(source_message_id),
            )
            .values(starboard_message_id=str(starboard_message_id))
        )


def set_count(
    guild_id: str, source_message_id: str, star_count: int, *, database_url: str | None = None
) -> None:
    """Store the recounted total.

    Set, not incremented: the caller has just read the live reaction count off
    the message, and an increment would drift permanently the first time a
    gateway event was missed or delivered twice.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            update(StarboardEntry)
            .where(
                StarboardEntry.guild_id == str(guild_id),
                StarboardEntry.source_message_id == str(source_message_id),
            )
            .values(star_count=int(star_count))
        )


def remove_entry(
    guild_id: str, source_message_id: str, *, database_url: str | None = None
) -> bool:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(
            delete(StarboardEntry).where(
                StarboardEntry.guild_id == str(guild_id),
                StarboardEntry.source_message_id == str(source_message_id),
            )
        )
    return bool(result.rowcount)


def delete_for_guild(guild_id: str, *, database_url: str | None = None) -> int:
    """Forget a guild's starboard entirely, for when the bot is removed."""
    engine = get_engine(database_url)
    with engine.begin() as connection:
        removed = connection.execute(
            delete(StarboardEntry).where(StarboardEntry.guild_id == str(guild_id))
        ).rowcount or 0
        connection.execute(
            delete(GuildStarboard).where(GuildStarboard.guild_id == str(guild_id))
        )
    return removed


def _normalise(row) -> dict:
    """Fill the defaults in, so no consumer has to know they exist.

    The columns are nullable following 0005's pattern, and a listener deciding
    whether five is the default is a listener that will disagree with the
    settings command about it.
    """
    data = dict(row)
    data["enabled"] = bool(data.get("enabled"))
    data["allow_self_star"] = bool(data.get("allow_self_star"))
    data["threshold"] = int(data.get("threshold") or DEFAULT_THRESHOLD)
    data["emoji"] = data.get("emoji") or DEFAULT_EMOJI
    data["ignored_channel_ids"] = [str(item) for item in (data.get("ignored_channel_ids") or [])]
    return data
