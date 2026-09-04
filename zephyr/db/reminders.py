"""Reminder storage and the claim.

Deliberately the same *shape* as ``zephyr/db/weather_subs.py`` -- one claim
inside one transaction, ``with_for_update(skip_locked=True)`` on Postgres only
-- because that idiom has already been reasoned about here and a second
scheduler inventing a different one is how two workers end up delivering the
same message twice.

It differs in one way, and the difference is the interesting part: due-ness is
a **SQL predicate**. ``weather_subs.is_due`` filters in Python because a wall
clock intent ("08:00 in Manila") depends on the row's own DST state and the set
is bounded by MAX_SUBS_PER_GUILD. A reminder is an *instant* and the row count
is unbounded, so filtering in Python would mean loading every future reminder
in the database on every tick.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, insert, select, update

from zephyr.core.logging import get_logger
from zephyr.db.models import Reminder
from zephyr.db.session import get_engine

log = get_logger(__name__)

# Per person. Enough for anybody using this as intended, low enough that one
# script cannot fill the table -- the same reasoning as MAX_SUBS_PER_GUILD.
MAX_PENDING_PER_USER = 50
# One tick's worth. A batch bounds how long a single delivery pass can take,
# so a backlog drains over several ticks rather than blocking the loop.
CLAIM_BATCH = 100
# A repeat this short would be a self-inflicted flood.
MIN_REPEAT_SECONDS = 600

_COLUMNS = (
    Reminder.id,
    Reminder.user_id,
    Reminder.guild_id,
    Reminder.channel_id,
    Reminder.message,
    Reminder.due_at,
    Reminder.tz,
    Reminder.repeat_every_seconds,
    Reminder.fired_at,
    Reminder.attempts,
    Reminder.source,
    Reminder.created_at,
)


class ReminderError(RuntimeError):
    """A refusal written for the person who caused it."""


def create(values: dict, *, database_url: str | None = None) -> dict:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        pending = connection.execute(
            select(Reminder.id)
            .where(Reminder.user_id == str(values["user_id"]), Reminder.fired_at.is_(None))
        ).all()
        if len(pending) >= MAX_PENDING_PER_USER:
            raise ReminderError(
                f"You already have {MAX_PENDING_PER_USER} reminders pending. "
                "Cancel one with /reminders before adding another."
            )
        reminder_id = connection.execute(insert(Reminder).values(**values)).inserted_primary_key[0]
    return get(reminder_id, database_url=database_url)


def get(reminder_id: int, *, database_url: str | None = None) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(*_COLUMNS).where(Reminder.id == int(reminder_id))
        ).mappings().first()
    return dict(row) if row else None


def list_pending(user_id: str, *, database_url: str | None = None) -> list[dict]:
    """One person's undelivered reminders, soonest first."""
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(*_COLUMNS)
            .where(Reminder.user_id == str(user_id), Reminder.fired_at.is_(None))
            .order_by(Reminder.due_at)
        ).mappings().all()
    return [dict(row) for row in rows]


def claim_due(now_utc: datetime, *, database_url: str | None = None) -> list[dict]:
    """Return the reminders due now, marking them claimed.

    Claiming inside the transaction is what makes SKIP LOCKED mean anything: a
    second worker reaching the same row after the first has claimed it sees
    ``fired_at`` set and no longer matches the predicate.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        statement = (
            select(*_COLUMNS)
            .where(Reminder.due_at <= now_utc, Reminder.fired_at.is_(None))
            .order_by(Reminder.due_at)
            .limit(CLAIM_BATCH)
        )
        if connection.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows = connection.execute(statement).mappings().all()
        if rows:
            connection.execute(
                update(Reminder)
                .where(Reminder.id.in_([row["id"] for row in rows]))
                .values(fired_at=now_utc, attempts=Reminder.attempts + 1)
            )
    return [dict(row) for row in rows]


def reschedule(reminder_id: int, *, from_time: datetime, database_url: str | None = None) -> dict | None:
    """Move a repeating reminder to its next occurrence and unclaim it.

    Advanced from the *previous due time* rather than from now, so a repeat
    stays on its original cadence instead of drifting by however long delivery
    took. Wound forward past any missed occurrences, because a bot that was
    offline for a day must not then fire a daily reminder twenty-four times.
    """
    row = get(reminder_id, database_url=database_url)
    if row is None or not row["repeat_every_seconds"]:
        return None

    interval = timedelta(seconds=int(row["repeat_every_seconds"]))
    next_due = _as_utc(row["due_at"]) + interval
    while next_due <= from_time:
        next_due += interval

    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            update(Reminder)
            .where(Reminder.id == int(reminder_id))
            .values(due_at=next_due, fired_at=None)
        )
    return get(reminder_id, database_url=database_url)


def cancel(reminder_id: int, user_id: str, *, database_url: str | None = None) -> bool:
    """Delete one reminder, if it belongs to ``user_id``.

    Scoped to the owner in the *statement* rather than by a read-then-delete:
    ids are sequential across the whole database, so without it a guess would
    cancel somebody else's reminder.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(
            delete(Reminder).where(
                Reminder.id == int(reminder_id), Reminder.user_id == str(user_id)
            )
        )
    return bool(result.rowcount)


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes even from a timezone=True column."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
