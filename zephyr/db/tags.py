"""Tag storage.

``normalise`` is the function to read first, because everything else depends on
it. A tag name is typed by a person and looked up by a person, and the two are
never quite the same string: ``Rules``, ``rules `` and ``RULES`` are one tag.
Normalising on the way *in* means the unique constraint enforces that, rather
than the read path having to lower-case both sides of every comparison and the
table quietly holding three rows that all answer to ``/tag rules``.

``create`` inserts and catches, for the reason ``mod_cases.record`` and
``starboard.claim`` do: a read-then-write races with itself, and here the loser
would silently shadow the winner.
"""

from __future__ import annotations

import re

from sqlalchemy import delete, desc, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from zephyr.db.models import Tag
from zephyr.db.session import get_engine

# Per guild. High enough for a real FAQ, low enough that one script cannot fill
# the table -- the same reasoning as MAX_SUBS_PER_GUILD and
# MAX_PENDING_PER_USER.
MAX_TAGS_PER_GUILD = 200

MAX_NAME_CHARS = 32
# Discord's message limit is 2000. The margin is for the reply's own framing.
MAX_CONTENT_CHARS = 1900

# Letters, digits, dashes and underscores. Deliberately narrow: a name
# containing a backtick or a mention breaks every listing it appears in, and a
# name containing whitespace cannot be typed into an autocomplete reliably.
_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]*$")

_COLUMNS = (
    Tag.id,
    Tag.guild_id,
    Tag.name,
    Tag.content,
    Tag.created_by,
    Tag.uses,
    Tag.created_at,
    Tag.updated_at,
)


class TagError(RuntimeError):
    """A refusal written for the person who caused it."""


def normalise(name: str) -> str | None:
    """The stored form of ``name``, or None when it is not a usable name."""
    text = str(name or "").strip().lower()
    if not text or len(text) > MAX_NAME_CHARS:
        return None
    return text if _NAME_PATTERN.match(text) else None


def get(guild_id: str, name: str, *, database_url: str | None = None) -> dict | None:
    key = normalise(name)
    if key is None:
        return None
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_COLUMNS).where(Tag.guild_id == str(guild_id), Tag.name == key)
        ).mappings().first()
    return dict(row) if row else None


def create(
    *,
    guild_id: str,
    name: str,
    content: str,
    created_by: str,
    database_url: str | None = None,
) -> dict:
    """Create one tag. Raises ``TagError`` on a bad name, a clash or the cap."""
    key = normalise(name)
    if key is None:
        raise TagError(
            f"A tag name has to be 1-{MAX_NAME_CHARS} characters of letters, numbers, "
            "dashes or underscores, and start with a letter or number."
        )
    body = str(content or "").strip()
    if not body:
        raise TagError("A tag needs some content.")
    body = body[:MAX_CONTENT_CHARS]

    engine = get_engine(database_url)
    try:
        with engine.begin() as connection:
            existing = connection.execute(
                select(func.count()).select_from(Tag).where(Tag.guild_id == str(guild_id))
            ).scalar()
            if int(existing or 0) >= MAX_TAGS_PER_GUILD:
                raise TagError(
                    f"This server already has {MAX_TAGS_PER_GUILD} tags. "
                    "Delete one before adding another."
                )
            connection.execute(
                insert(Tag).values(
                    guild_id=str(guild_id),
                    name=key,
                    content=body,
                    created_by=str(created_by),
                    uses=0,
                )
            )
    except IntegrityError:
        # The constraint, not a prior read, is what decides this: two people
        # creating the same tag at once both read "no such tag", and without the
        # catch the loser would silently shadow the winner.
        raise TagError(f"A tag called `{key}` already exists here.") from None
    return get(guild_id, key, database_url=database_url)


def edit(
    guild_id: str, name: str, content: str, *, database_url: str | None = None
) -> dict | None:
    key = normalise(name)
    if key is None:
        return None
    body = str(content or "").strip()
    if not body:
        raise TagError("A tag needs some content.")

    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(
            update(Tag)
            .where(Tag.guild_id == str(guild_id), Tag.name == key)
            .values(content=body[:MAX_CONTENT_CHARS])
        )
    if not result.rowcount:
        return None
    return get(guild_id, key, database_url=database_url)


def remove(guild_id: str, name: str, *, database_url: str | None = None) -> bool:
    key = normalise(name)
    if key is None:
        return False
    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(
            delete(Tag).where(Tag.guild_id == str(guild_id), Tag.name == key)
        )
    return bool(result.rowcount)


def record_use(guild_id: str, name: str, *, database_url: str | None = None) -> None:
    """Count one invocation.

    Incremented in the statement rather than read-modify-written, so two people
    invoking a tag at the same moment do not lose one of the two counts.
    """
    key = normalise(name)
    if key is None:
        return
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            update(Tag)
            .where(Tag.guild_id == str(guild_id), Tag.name == key)
            .values(uses=Tag.uses + 1)
        )


def list_for_guild(
    guild_id: str, *, prefix: str | None = None, limit: int = 25, database_url: str | None = None
) -> list[dict]:
    """A guild's tags, most-used first.

    Most-used rather than alphabetical because this backs both ``/tag-list`` and
    the autocomplete, and the useful first suggestion is the tag people actually
    invoke -- not the one whose name starts with "a".
    """
    statement = (
        select(Tag.name, Tag.content, Tag.uses, Tag.created_by)
        .where(Tag.guild_id == str(guild_id))
        .order_by(desc(Tag.uses), Tag.name)
        .limit(max(1, min(int(limit), MAX_TAGS_PER_GUILD)))
    )
    if prefix:
        key = str(prefix).strip().lower()
        if key:
            # A prefix match, not a substring: the names are short and a
            # substring match on "e" would return most of the table.
            statement = statement.where(Tag.name.startswith(key))
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()
    return [dict(row) for row in rows]


def count_for_guild(guild_id: str, *, database_url: str | None = None) -> int:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        return int(
            connection.execute(
                select(func.count()).select_from(Tag).where(Tag.guild_id == str(guild_id))
            ).scalar()
            or 0
        )


def delete_for_guild(guild_id: str, *, database_url: str | None = None) -> int:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        return connection.execute(
            delete(Tag).where(Tag.guild_id == str(guild_id))
        ).rowcount or 0
