"""Moderation case storage.

Two things here differ from ``zephyr/db/audit.py``, and both are deliberate.

**This one is not fail-soft.** ``audit.record`` swallows its own errors because
the settings change it describes has already happened, so raising would report a
success as a failure. A moderation case is not a description of something else --
it *is* the record. ``/cases`` reads it back, ``/reason`` amends it, and a
moderator relies on "three prior warnings" being true. A dropped row here is a
warning that provably never happened, so a failure has to reach the caller,
which then tells the moderator the action was taken but not recorded.

**The case number is allocated, not generated.** ``MAX(case_number) + 1`` inside
the inserting transaction is a read-then-write race by construction: two
moderators acting in the same second read the same maximum. The unique
constraint on ``(guild_id, case_number)`` is what makes that safe -- the
database rejects the loser and ``record`` retries with a fresh maximum. A
sequence would avoid the race but would number cases globally, which is both
unreadable ("case 4,178") and a slow leak of how busy every other guild is.
"""

from __future__ import annotations

from sqlalchemy import desc, func, insert, select, update
from sqlalchemy.exc import IntegrityError

from zephyr.core.logging import get_logger
from zephyr.db.models import ModCase
from zephyr.db.session import get_engine

log = get_logger(__name__)

# The actions that may be recorded. An allow-list rather than free text: these
# strings are rendered into a case embed and filtered on, and a typo'd action
# would be invisible to every filter that looks for the correct spelling.
ACTIONS = frozenset({"warn", "timeout", "untimeout", "kick", "ban", "unban", "purge"})

# A reason is written into an embed field, which Discord caps at 1024. Trimmed
# on the way in rather than on the way out, so what is stored is what will be
# shown.
MAX_REASON_CHARS = 900

# How many times to re-read the maximum after losing the allocation race. Three
# is generous: each retry only loses to a *simultaneous* insert in the same
# guild, and a guild with four moderators pressing enter in the same
# millisecond has a different problem.
ALLOCATION_ATTEMPTS = 3

DEFAULT_LIMIT = 25
MAX_LIMIT = 100

_COLUMNS = (
    ModCase.id,
    ModCase.guild_id,
    ModCase.case_number,
    ModCase.action,
    ModCase.target_id,
    ModCase.target_tag,
    ModCase.moderator_id,
    ModCase.reason,
    ModCase.duration_seconds,
    ModCase.created_at,
)


def record(
    *,
    guild_id: str,
    action: str,
    target_id: str,
    moderator_id: str,
    target_tag: str | None = None,
    reason: str | None = None,
    duration_seconds: int | None = None,
    database_url: str | None = None,
) -> dict:
    """Write one case and return it, numbered within its guild.

    Raises rather than swallowing -- see the module docstring.
    """
    if action not in ACTIONS:
        raise ValueError(f"Unknown moderation action {action!r}")

    engine = get_engine(database_url)
    trimmed = _trim(reason)
    last_error: Exception | None = None
    case_number = 0

    for _ in range(ALLOCATION_ATTEMPTS):
        try:
            with engine.begin() as connection:
                # Read and insert in one transaction. The constraint, not this
                # read, is what guarantees uniqueness; the read only makes the
                # common case a single round trip.
                case_number = _next_case_number(connection, str(guild_id))
                case_id = connection.execute(
                    insert(ModCase).values(
                        guild_id=str(guild_id),
                        case_number=case_number,
                        action=action,
                        target_id=str(target_id),
                        target_tag=target_tag,
                        moderator_id=str(moderator_id),
                        reason=trimmed,
                        duration_seconds=duration_seconds,
                    )
                ).inserted_primary_key[0]
            return get_by_id(case_id, database_url=database_url)
        except IntegrityError as exc:
            # Somebody else took this number between the read and the insert.
            last_error = exc
            log.warning("Case number %s was taken in guild %s; retrying", case_number, guild_id)

    raise RuntimeError("Could not allocate a case number") from last_error


def _next_case_number(connection, guild_id: str) -> int:
    """The number to try next.

    A separate function so the retry above can be tested: the interesting case
    is losing the race *once* and then succeeding, and that needs a seam where
    a stale maximum can be returned deliberately.
    """
    highest = connection.execute(
        select(func.max(ModCase.case_number)).where(ModCase.guild_id == guild_id)
    ).scalar()
    return int(highest or 0) + 1


def get_by_id(case_id: int, *, database_url: str | None = None) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_COLUMNS).where(ModCase.id == int(case_id))
        ).mappings().first()
    return _serialise(row) if row else None


def get(guild_id: str, case_number: int, *, database_url: str | None = None) -> dict | None:
    """One case, addressed the way a moderator addresses it.

    Scoped to the guild in the statement: case numbers restart per guild, so a
    lookup without it would answer with somebody else's server's case 12.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_COLUMNS).where(
                ModCase.guild_id == str(guild_id), ModCase.case_number == int(case_number)
            )
        ).mappings().first()
    return _serialise(row) if row else None


def read(
    guild_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    before_number: int | None = None,
    action: str | None = None,
    target_id: str | None = None,
    moderator_id: str | None = None,
    database_url: str | None = None,
) -> dict:
    """One page of a guild's cases, newest first.

    Keyset-paginated on ``case_number`` rather than offset, for the reason
    ``audit.read`` gives: the table only grows, and an offset drifts under an
    active guild. Filtering happens here rather than in the caller because the
    page is keyset-paginated -- a filter applied after pagination can only
    narrow the twenty-five rows it already has, so "every warning this person
    has" would mean paging through the whole history by hand.
    """
    page = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    statement = (
        select(*_COLUMNS)
        .where(ModCase.guild_id == str(guild_id))
        .order_by(desc(ModCase.case_number))
        .limit(page + 1)
    )
    if before_number is not None:
        statement = statement.where(ModCase.case_number < int(before_number))
    if action:
        statement = statement.where(ModCase.action == str(action))
    if target_id:
        statement = statement.where(ModCase.target_id == str(target_id))
    if moderator_id:
        statement = statement.where(ModCase.moderator_id == str(moderator_id))

    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    has_more = len(rows) > page
    entries = [_serialise(row) for row in rows[:page]]
    return {
        "entries": entries,
        "next_before": entries[-1]["case_number"] if has_more and entries else None,
    }


def count_for_target(
    guild_id: str, target_id: str, *, action: str | None = None, database_url: str | None = None
) -> int:
    """How many cases this person already has.

    A count rather than a page, because the useful thing to put in front of a
    moderator about to act is "this is their fourth warning" -- and a page read
    to be counted would be capped at MAX_LIMIT and quietly under-report.
    """
    statement = select(func.count()).select_from(ModCase).where(
        ModCase.guild_id == str(guild_id), ModCase.target_id == str(target_id)
    )
    if action:
        statement = statement.where(ModCase.action == str(action))
    engine = get_engine(database_url)
    with engine.connect() as connection:
        return int(connection.execute(statement).scalar() or 0)


def set_reason(
    guild_id: str, case_number: int, reason: str, *, database_url: str | None = None
) -> dict | None:
    """Attach or replace a reason after the fact.

    The common real sequence is a moderator acting fast and explaining a minute
    later, so this exists rather than making ``reason`` required at the point of
    action -- a required field that gets filled with "." is worse than a NULL
    that gets filled in properly.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(
            update(ModCase)
            .where(ModCase.guild_id == str(guild_id), ModCase.case_number == int(case_number))
            .values(reason=_trim(reason))
        )
    if not result.rowcount:
        return None
    return get(guild_id, case_number, database_url=database_url)


def _trim(reason: str | None) -> str | None:
    if reason is None:
        return None
    text = str(reason).strip()
    # "" becomes NULL: an empty reason and no reason are the same thing, and
    # storing both would make "has a reason" two different checks.
    return text[:MAX_REASON_CHARS] or None


def _serialise(row) -> dict:
    created = row["created_at"]
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "case_number": row["case_number"],
        "action": row["action"],
        "target_id": row["target_id"],
        "target_tag": row["target_tag"],
        "moderator_id": row["moderator_id"],
        "reason": row["reason"],
        "duration_seconds": row["duration_seconds"],
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }
