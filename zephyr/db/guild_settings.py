"""Per-guild settings, shared by the bot and the web tier.

This lived in ``website/repo.py`` while the dashboard was its only reader.  The
bot now reads it too -- ``dj_role_id`` decides who may drive the player from the
browser, and that decision has to be made by the bot, which is the only process
that can see the actor's live roles.  A shared table belongs in the shared layer;
``website/repo.py`` keeps ``web_users``, which is genuinely web-only.

SQLAlchemy Core, like the rest of ``zephyr/db``.  Synchronous, so the bot calls
these through ``asyncio.to_thread``.
"""

from sqlalchemy import select

from zephyr.db.models import Guild
from zephyr.db.session import get_engine

# Everything a caller may write.  An explicit list, not "whatever keys arrived":
# a PATCH body is user input, and `id` in particular must never be assignable.
WRITABLE_COLUMNS = (
    "prefix",
    "locale",
    "timezone",
    "default_volume",
    "dj_role_id",
    "music_channel_ids",
)

_COLUMNS = (
    Guild.id,
    Guild.prefix,
    Guild.locale,
    Guild.timezone,
    Guild.default_volume,
    Guild.dj_role_id,
    Guild.music_channel_ids,
    Guild.enabled_cogs,
)


def read_guild_settings(guild_id: str, *, database_url: str | None = None) -> dict | None:
    """Return a guild's stored settings, or None when it has never been configured.

    None is not a statement about bot membership -- a row only appears once
    somebody saves settings.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(select(*_COLUMNS).where(Guild.id == str(guild_id))).mappings().first()
    return dict(row) if row else None


def write_guild_settings(guild_id: str, values: dict, *, database_url: str | None = None) -> dict:
    """Create or update a guild's settings and return the stored row.

    ON CONFLICT DO UPDATE rather than UPDATE-then-INSERT-if-zero-rows, which is
    race-prone -- the same reasoning as ``upsert_web_user``.  Only the keys
    present in ``values`` are written, so a PATCH of one field cannot blank the
    others by omission.
    """
    filtered = {key: value for key, value in values.items() if key in WRITABLE_COLUMNS}
    engine = get_engine(database_url)
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert
        else:
            from sqlalchemy.dialects.sqlite import insert

        statement = insert(Guild.__table__).values(id=str(guild_id), **filtered)
        if filtered:
            statement = statement.on_conflict_do_update(
                index_elements=["id"],
                set_={key: statement.excluded[key] for key in filtered},
            )
        else:
            # Nothing to write, but the row must exist so the caller stops being
            # told its settings are defaults.
            statement = statement.on_conflict_do_nothing(index_elements=["id"])
        connection.execute(statement)
    return read_guild_settings(guild_id, database_url=database_url) or {}


def read_dj_role_id(guild_id: str, *, database_url: str | None = None) -> str | None:
    """Just the DJ role, for the permission check on every bridge command."""
    engine = get_engine(database_url)
    with engine.connect() as connection:
        return connection.execute(
            select(Guild.dj_role_id).where(Guild.id == str(guild_id))
        ).scalar_one_or_none()


def read_dj_roles(*, database_url: str | None = None) -> dict[str, str]:
    """Every configured DJ role, keyed by guild id.

    One query instead of one per guild: the bot caches this and re-reads it on a
    slow loop, because the permission check runs on every button press and every
    bridge command and cannot afford a round trip there.  Guilds with no DJ role
    are simply absent.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(Guild.id, Guild.dj_role_id).where(Guild.dj_role_id.is_not(None))
        ).all()
    return {str(row.id): str(row.dj_role_id) for row in rows}
