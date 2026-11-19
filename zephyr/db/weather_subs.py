"""Weather subscriptions and per-user weather defaults.

The interesting part is ``claim_due``: selecting what is due and marking it run
must be one transaction, or two bot processes racing the same minute post the
same digest twice.  On Postgres the select takes ``FOR UPDATE SKIP LOCKED`` so
the second process sees an empty set instead of blocking; SQLite has neither
clause and no concurrent writers to need them, so it degrades to a plain select
inside the same transaction.  Getting that wrong would break every test and every
local run, since the default database is SQLite.

Due-ness itself is computed in Python rather than in SQL.  It depends on each
row's own timezone -- including whether that zone is currently in DST -- and a
deployment has tens of subscriptions, not millions, so a comprehension is both
clearer and correct where a portable SQL expression would be neither.
"""

from datetime import date, datetime, time, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, insert, select, update

from zephyr.db.models import BotUser, WeatherSub
from zephyr.db.session import get_engine

KINDS = ("daily", "severe", "class_suspension")
SCHEDULED_KINDS = ("daily",)
WATCHED_KINDS = ("severe", "class_suspension")
MAX_SUBS_PER_GUILD = 25

_COLUMNS = (
    WeatherSub.id,
    WeatherSub.guild_id,
    WeatherSub.channel_id,
    WeatherSub.kind,
    WeatherSub.location,
    WeatherSub.lat,
    WeatherSub.lon,
    WeatherSub.units,
    WeatherSub.schedule_local_time,
    WeatherSub.tz,
    WeatherSub.thresholds,
    WeatherSub.enabled,
    WeatherSub.last_run_at,
    WeatherSub.last_fingerprint,
    WeatherSub.created_at,
)


class SubscriptionError(RuntimeError):
    """A subscription operation failed for a reason worth showing a user."""


def zone(name: str | None):
    """``ZoneInfo`` for ``name``, falling back to UTC.

    A subscription with an unloadable zone must still fire.  Silently posting an
    hour late in UTC is a much smaller failure than never posting again because
    the host is missing a tzdata entry.
    """
    try:
        return ZoneInfo(name or "UTC")
    # OSError: a zone *folder* such as "America" raises PermissionError on
    # Windows and IsADirectoryError on Linux, which was a 500 on the dashboard.
    except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError, OSError):
        return timezone.utc


def normalise_zone(name: str | None) -> tuple[str, bool]:
    """Return ``(zone name, was_accepted)``.

    Two values because the caller usually wants to say so: quietly storing UTC
    when somebody typed "Manila" would look like the setting had been ignored,
    which it has.
    """
    candidate = (name or "UTC").strip()
    try:
        ZoneInfo(candidate)
        return candidate, True
    # OSError: a zone *folder* such as "America" raises PermissionError on
    # Windows and IsADirectoryError on Linux, which was a 500 on the dashboard.
    except (ZoneInfoNotFoundError, ValueError, ModuleNotFoundError, OSError):
        return "UTC", False


def parse_local_time(value: str | None) -> time:
    """Parse "HH:MM"; raise ``SubscriptionError`` on anything else."""
    try:
        hour, minute = str(value).split(":")
        return time(int(hour), int(minute))
    except (AttributeError, TypeError, ValueError):
        raise SubscriptionError("A schedule must look like 08:00.") from None


def is_due(row, now_utc: datetime) -> bool:
    """Has this scheduled subscription's local time passed today, unfired?

    "Today" is the subscriber's local day, not the server's, so a digest set for
    08:00 in Manila fires once per Manila day regardless of where the bot runs.
    Comparing the *local date* of the last run rather than an elapsed interval is
    what keeps that true across a DST shift, where consecutive local days are 23
    or 25 hours apart.
    """
    if not row.schedule_local_time:
        return False
    try:
        scheduled = parse_local_time(row.schedule_local_time)
    except SubscriptionError:
        return False
    tzinfo = zone(row.tz)
    local_now = now_utc.astimezone(tzinfo)
    if local_now.time() < scheduled:
        return False
    return _local_date_of(row.last_run_at, tzinfo) != local_now.date()


def _local_date_of(moment: datetime | None, tzinfo) -> date | None:
    if moment is None:
        return None
    # SQLite hands back naive datetimes even from a timezone=True column, and
    # astimezone() on a naive value would assume the *server's* zone -- which is
    # how a UTC timestamp silently becomes a local one.
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(tzinfo).date()


def claim_due(now_utc: datetime, *, database_url: str | None = None) -> list[dict]:
    """Return the scheduled subscriptions due now, marking them run.

    Claiming inside the locked transaction is what makes SKIP LOCKED mean
    anything: a second process reaching the same row after the first has claimed
    it finds last_run_at already advanced and computes it as not due.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        statement = select(*_COLUMNS).where(
            WeatherSub.enabled.is_(True), WeatherSub.kind.in_(SCHEDULED_KINDS)
        )
        if connection.dialect.name == "postgresql":
            statement = statement.with_for_update(skip_locked=True)
        rows = connection.execute(statement).all()
        due = [row for row in rows if is_due(row, now_utc)]
        if due:
            connection.execute(
                update(WeatherSub)
                .where(WeatherSub.id.in_([row.id for row in due]))
                .values(last_run_at=now_utc)
            )
    return [dict(row._mapping) for row in due]


def release_claim(sub_id: int, previous_run_at: datetime | None, *, database_url: str | None = None) -> None:
    """Undo ``claim_due`` for a digest that could not be delivered.

    The claim marks a digest run *before* it is posted, so a provider hiccup or
    a failed send at 08:00 used to lose that day's digest silently.  Restoring
    the previous ``last_run_at`` makes it due again on the next tick.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            update(WeatherSub).where(WeatherSub.id == sub_id).values(last_run_at=previous_run_at)
        )


def list_watched(*, database_url: str | None = None) -> list[dict]:
    """Every enabled severe / class-suspension subscription.

    Not claimed: these are deduplicated by fingerprint rather than by time, so a
    tick that finds nothing new must leave the row untouched.
    """
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(*_COLUMNS).where(WeatherSub.enabled.is_(True), WeatherSub.kind.in_(WATCHED_KINDS))
        ).all()
    return [dict(row._mapping) for row in rows]


def mark_fired(
    sub_id: int, *, fingerprint: str | None = None, at: datetime | None = None,
    database_url: str | None = None,
) -> None:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(
            update(WeatherSub)
            .where(WeatherSub.id == sub_id)
            .values(last_run_at=at or datetime.now(timezone.utc), last_fingerprint=fingerprint)
        )


def clear_fingerprint(sub_id: int, *, database_url: str | None = None) -> None:
    """Forget the last alert once conditions are quiet again.

    The severe fingerprint has no date, so without this Tuesday's afternoon
    storm matched Monday's and was never announced.
    """
    engine = get_engine(database_url)
    with engine.begin() as connection:
        connection.execute(update(WeatherSub).where(WeatherSub.id == sub_id).values(last_fingerprint=None))


def list_for_guild(guild_id: str, *, database_url: str | None = None) -> list[dict]:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(
            select(*_COLUMNS).where(WeatherSub.guild_id == str(guild_id)).order_by(WeatherSub.id)
        ).all()
    return [dict(row._mapping) for row in rows]


def get(sub_id: int, *, database_url: str | None = None) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(select(*_COLUMNS).where(WeatherSub.id == sub_id)).first()
    return dict(row._mapping) if row else None


def create(values: dict, *, database_url: str | None = None) -> dict:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        existing = connection.execute(
            select(WeatherSub.id).where(WeatherSub.guild_id == str(values["guild_id"]))
        ).all()
        if len(existing) >= MAX_SUBS_PER_GUILD:
            raise SubscriptionError(f"A server may have at most {MAX_SUBS_PER_GUILD} subscriptions.")
        if _duplicate_of(connection, values) is not None:
            raise SubscriptionError("That channel already has the same subscription.")
        if values.get("kind") in SCHEDULED_KINDS and values.get("last_run_at") is None:
            # A digest created after today's time has passed would otherwise be
            # "due" at once: an 08:00 digest made at 15:00 posted within a minute.
            values = _skip_today_if_passed(values, None)
        sub_id = connection.execute(insert(WeatherSub).values(**values).returning(WeatherSub.id)).scalar_one()
    return get(sub_id, database_url=database_url)


def _skip_today_if_passed(values: dict, last_run_at) -> dict:
    """Mark a digest as run today when its time has already passed."""
    now = datetime.now(timezone.utc)
    probe = SimpleNamespace(schedule_local_time=values.get("schedule_local_time"),
                            tz=values.get("tz"), last_run_at=last_run_at)
    if is_due(probe, now):
        return {**values, "last_run_at": now}
    return values


def _duplicate_of(connection, values: dict) -> int | None:
    """The id of an identical subscription in the same channel, if any."""
    rows = connection.execute(
        select(WeatherSub.id, WeatherSub.lat, WeatherSub.lon, WeatherSub.schedule_local_time).where(
            WeatherSub.guild_id == str(values.get("guild_id")),
            WeatherSub.channel_id == str(values.get("channel_id")),
            WeatherSub.kind == values.get("kind"),
        )
    ).all()
    for row in rows:
        if (abs(float(row.lat) - float(values.get("lat", 0))) < 1e-4
                and abs(float(row.lon) - float(values.get("lon", 0))) < 1e-4
                and (row.schedule_local_time or None) == (values.get("schedule_local_time") or None)):
            return row.id
    return None


# Edits that can make a digest "due" again at once.
_SCHEDULE_FIELDS = {"kind", "schedule_local_time", "tz", "enabled"}


def update_sub(sub_id: int, values: dict, *, database_url: str | None = None) -> dict | None:
    if values:
        engine = get_engine(database_url)
        with engine.begin() as connection:
            if _SCHEDULE_FIELDS & set(values) and "last_run_at" not in values:
                row = connection.execute(select(*_COLUMNS).where(WeatherSub.id == sub_id)).first()
                if row is not None:
                    merged = {**dict(row._mapping), **values}
                    if merged["kind"] in SCHEDULED_KINDS and merged["enabled"]:
                        # The same rule create() applies: re-enabling an 08:00
                        # digest at 15:00, or moving it to a time already past,
                        # used to post it within a minute.
                        probed = _skip_today_if_passed(merged, merged.get("last_run_at"))
                        if probed is not merged:
                            values = {**values, "last_run_at": probed["last_run_at"]}
            connection.execute(update(WeatherSub).where(WeatherSub.id == sub_id).values(**values))
    return get(sub_id, database_url=database_url)


def delete_sub(sub_id: int, *, database_url: str | None = None) -> bool:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(delete(WeatherSub).where(WeatherSub.id == sub_id))
    return result.rowcount > 0


# ---------------------------------------------------------------------------
# Per-user defaults
# ---------------------------------------------------------------------------


def read_bot_user(discord_id: str, *, database_url: str | None = None) -> dict | None:
    engine = get_engine(database_url)
    with engine.connect() as connection:
        row = connection.execute(
            select(
                BotUser.discord_id, BotUser.default_city, BotUser.lat, BotUser.lon,
                BotUser.units, BotUser.timezone,
            ).where(BotUser.discord_id == str(discord_id))
        ).mappings().first()
    return dict(row) if row else None


def write_bot_user(discord_id: str, values: dict, *, database_url: str | None = None) -> dict:
    """Upsert a user's weather defaults.  Only the keys given are written."""
    allowed = {"default_city", "lat", "lon", "units", "timezone"}
    filtered = {key: value for key, value in values.items() if key in allowed}
    engine = get_engine(database_url)
    with engine.begin() as connection:
        if connection.dialect.name == "postgresql":
            from sqlalchemy.dialects.postgresql import insert as dialect_insert
        else:
            from sqlalchemy.dialects.sqlite import insert as dialect_insert

        statement = dialect_insert(BotUser.__table__).values(discord_id=str(discord_id), **filtered)
        if filtered:
            statement = statement.on_conflict_do_update(
                index_elements=["discord_id"],
                set_={key: statement.excluded[key] for key in filtered},
            )
        else:
            statement = statement.on_conflict_do_nothing(index_elements=["discord_id"])
        connection.execute(statement)
    return read_bot_user(discord_id, database_url=database_url) or {}


def clear_bot_user(discord_id: str, *, database_url: str | None = None) -> bool:
    engine = get_engine(database_url)
    with engine.begin() as connection:
        result = connection.execute(delete(BotUser).where(BotUser.discord_id == str(discord_id)))
    return result.rowcount > 0
