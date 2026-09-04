"""Activity totals, daily distinct actives, and the level curve.

``flush`` is the interesting function, and the interesting thing about it is
that it is **additive and batched**. Nothing in this module is called from a
message handler; the cog accumulates in memory and hands a whole batch over on a
loop. See ``zephyr/cogs/activity.py`` for why.

Additive matters because the batch is a delta, not a state: the cog has counted
"three more messages from this person", not "this person has forty-one". An
upsert that *replaced* the total would lose whatever another process had written
since the batch started, and would reset the total to the batch size after a
restart.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone

from sqlalchemy import delete, desc, func, select, tuple_

from zephyr.db.models import ActivityDailyUser, ActivityTotal, GuildActivity
from zephyr.db.session import get_engine

# XP per counted message. Fixed rather than random: a random award is a common
# choice in leveling bots and makes every test of the curve probabilistic for
# no benefit anybody can observe.
XP_PER_MESSAGE = 10

# Cumulative XP for level n is 50 * n * (n + 1): 100 for level 1, 300 for 2,
# 600 for 3. Quadratic, so early levels come quickly and later ones do not --
# which is the whole point of a curve rather than a threshold.
LEVEL_COEFFICIENT = 50

CONFIG_COLUMNS = (
    "enabled",
    "announce_channel_id",
    "announce_level_ups",
    "ignored_channel_ids",
)

_CONFIG = (
    GuildActivity.guild_id,
    GuildActivity.enabled,
    GuildActivity.announce_channel_id,
    GuildActivity.announce_level_ups,
    GuildActivity.ignored_channel_ids,
)


def xp_for_level(level: int) -> int:
    """The cumulative XP needed to reach ``level``."""
    level = max(0, int(level))
    return LEVEL_COEFFICIENT * level * (level + 1)


def level_for_xp(xp: int) -> int:
    """The level ``xp`` buys.

    Closed form with a correction loop rather than a bare formula: the inverse
    of a quadratic goes through a float, and a level that is off by one at
    exactly the boundary is the case people notice -- somebody sitting on 300 XP
    being told they are level 1.
    """
    xp = max(0, int(xp))
    if xp < xp_for_level(1):
        return 0
    level = int((math.sqrt(1 + xp / (LEVEL_COEFFICIENT / 2)) - 1) / 2)
    while xp_for_level(level + 1) <= xp:
        level += 1
    while level > 0 and xp_for_level(level) > xp:
        level -= 1
    return level


def progress(xp: int) -> tuple[int, int, int]:
    """(level, xp into this level, xp this level needs)."""
    level = level_for_xp(xp)
    floor_xp = xp_for_level(level)
    return level, int(xp) - floor_xp, xp_for_level(level + 1) - floor_xp


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def read_config(guild_id: str, *, database_url: str | None = None) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_CONFIG).where(GuildActivity.guild_id == str(guild_id))
        ).mappings().first()
    return _normalise(row) if row else None


def write_config(guild_id: str, values: dict, *, database_url: str | None = None) -> dict:
    filtered = {key: value for key, value in values.items() if key in CONFIG_COLUMNS}
    engine = get_engine(database_url)
    with engine.begin() as connection:
        upsert = _upsert(connection)
        statement = upsert(GuildActivity.__table__).values(guild_id=str(guild_id), **filtered)
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
    """Every guild with activity tracking on, keyed by guild id."""
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(*_CONFIG).where(GuildActivity.enabled.is_(True))
        ).mappings().all()
    return {str(row["guild_id"]): _normalise(row) for row in rows}


# ---------------------------------------------------------------------------
# The flush
# ---------------------------------------------------------------------------


def flush(batch: dict, *, now: datetime | None = None, database_url: str | None = None) -> dict:
    """Apply one accumulated batch.

    Returns ``{"touched": n, "level_ups": [{guild_id, user_id, level}, ...]}``.

    The level-ups come from here rather than from the cog because a level is a
    function of the *stored* total. Computing it from the in-memory delta would
    announce a level the database does not yet agree with -- and announce it
    again after a restart.

    ``batch`` is ``{(guild_id, user_id): message_count}`` -- a *delta*, which is
    why every write here adds rather than assigns. The cog has counted "three
    more from this person", not "this person has forty-one", and an assigning
    upsert would reset the stored total to the batch size on the first flush
    after a restart.

    One transaction for the whole batch, so a crash mid-flush loses the batch
    rather than half-applying it. Losing a batch costs a few minutes of counts,
    which is an acceptable price for a leaderboard and much cheaper than
    reconciling a partial write.
    """
    if not batch:
        return {"touched": 0, "level_ups": []}
    stamp = now or datetime.now(timezone.utc)
    day = stamp.strftime("%Y-%m-%d")
    level_ups = []

    engine = get_engine(database_url)
    with engine.begin() as connection:
        upsert = _upsert(connection)
        # One query for every member in the batch, not one per member: the
        # batch can hold thousands, and the previous XP is only needed to spot
        # a level crossing.
        keys = [(str(guild_id), str(user_id)) for guild_id, user_id in batch]
        previous = {
            (row["guild_id"], row["user_id"]): row["xp"]
            for row in connection.execute(
                select(ActivityTotal.guild_id, ActivityTotal.user_id, ActivityTotal.xp)
                .where(tuple_(ActivityTotal.guild_id, ActivityTotal.user_id).in_(keys))
            ).mappings()
        }
        for (guild_id, user_id), count in batch.items():
            count = int(count)
            if count <= 0:
                continue
            totals = upsert(ActivityTotal.__table__).values(
                guild_id=str(guild_id),
                user_id=str(user_id),
                messages=count,
                xp=count * XP_PER_MESSAGE,
                last_message_at=stamp,
            )
            connection.execute(
                totals.on_conflict_do_update(
                    index_elements=["guild_id", "user_id"],
                    set_={
                        "messages": ActivityTotal.messages + count,
                        "xp": ActivityTotal.xp + count * XP_PER_MESSAGE,
                        "last_message_at": stamp,
                    },
                )
            )
            before = int(previous.get((str(guild_id), str(user_id)), 0))
            after = before + count * XP_PER_MESSAGE
            if level_for_xp(after) > level_for_xp(before):
                level_ups.append(
                    {
                        "guild_id": str(guild_id),
                        "user_id": str(user_id),
                        "level": level_for_xp(after),
                    }
                )

            daily = upsert(ActivityDailyUser.__table__).values(
                guild_id=str(guild_id), day=day, user_id=str(user_id), messages=count
            )
            connection.execute(
                daily.on_conflict_do_update(
                    index_elements=["guild_id", "day", "user_id"],
                    set_={"messages": ActivityDailyUser.messages + count},
                )
            )
    return {"touched": len(batch), "level_ups": level_ups}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_member(guild_id: str, user_id: str, *, database_url: str | None = None) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(ActivityTotal.messages, ActivityTotal.xp, ActivityTotal.last_message_at)
            .where(
                ActivityTotal.guild_id == str(guild_id),
                ActivityTotal.user_id == str(user_id),
            )
        ).mappings().first()
    if row is None:
        return None
    level, into, needed = progress(row["xp"])
    return {
        "messages": row["messages"],
        "xp": row["xp"],
        "level": level,
        "xp_into_level": into,
        "xp_for_next_level": needed,
        "last_message_at": row["last_message_at"],
    }


def rank_of(guild_id: str, user_id: str, *, database_url: str | None = None) -> int | None:
    """One-based position on the leaderboard, or None if they have no row.

    Counted with a COUNT rather than by reading the leaderboard and searching
    it: a member in position 4,000 is a real case, and a page-based rank would
    either be wrong or would page through the whole table to find them.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        own = connection.execute(
            select(ActivityTotal.xp).where(
                ActivityTotal.guild_id == str(guild_id),
                ActivityTotal.user_id == str(user_id),
            )
        ).scalar()
        if own is None:
            return None
        ahead = connection.execute(
            select(func.count()).select_from(ActivityTotal).where(
                ActivityTotal.guild_id == str(guild_id),
                ActivityTotal.xp > own,
            )
        ).scalar()
    return int(ahead or 0) + 1


def leaderboard(guild_id: str, *, limit: int = 10, database_url: str | None = None) -> list[dict]:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(ActivityTotal.user_id, ActivityTotal.messages, ActivityTotal.xp)
            .where(ActivityTotal.guild_id == str(guild_id))
            .order_by(desc(ActivityTotal.xp))
            .limit(max(1, min(int(limit), 25)))
        ).mappings().all()
    return [
        {
            "user_id": row["user_id"],
            "messages": row["messages"],
            "xp": row["xp"],
            "level": level_for_xp(row["xp"]),
        }
        for row in rows
    ]


def daily_summary(guild_id: str, day: str, *, database_url: str | None = None) -> dict:
    """Messages and distinct speakers on one day.

    Both from the same table: ``active`` is a row count and ``messages`` is the
    sum of those rows, which is the reason there is no separate daily-rollup
    table to keep in step.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(
                func.count().label("active"),
                func.coalesce(func.sum(ActivityDailyUser.messages), 0).label("messages"),
            ).where(
                ActivityDailyUser.guild_id == str(guild_id),
                ActivityDailyUser.day == str(day),
            )
        ).mappings().first()
    return {"day": str(day), "active": int(row["active"] or 0), "messages": int(row["messages"] or 0)}


def delete_for_guild(guild_id: str, *, database_url: str | None = None) -> int:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        removed = connection.execute(
            delete(ActivityTotal).where(ActivityTotal.guild_id == str(guild_id))
        ).rowcount or 0
        connection.execute(
            delete(ActivityDailyUser).where(ActivityDailyUser.guild_id == str(guild_id))
        )
        connection.execute(
            delete(GuildActivity).where(GuildActivity.guild_id == str(guild_id))
        )
    return removed


def _upsert(connection):
    if connection.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert


def _normalise(row) -> dict:
    data = dict(row)
    data["enabled"] = bool(data.get("enabled"))
    # Announcing is the default when tracking is on: a level nobody is told
    # about is a number in a database.
    data["announce_level_ups"] = (
        True if data.get("announce_level_ups") is None else bool(data["announce_level_ups"])
    )
    data["ignored_channel_ids"] = [str(item) for item in (data.get("ignored_channel_ids") or [])]
    return data
