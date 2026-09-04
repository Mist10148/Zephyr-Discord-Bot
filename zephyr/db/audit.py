"""Append-only audit trail for mutating actions.

Deliberately fail-soft, and the only module here that is: an audit write must
never be the reason a settings change or a skip fails.  The action itself has
already happened by the time this is called, so raising would report a success as
a failure and invite the user to repeat it.  Losing a log line is the cheaper
failure, and it is logged to stderr where a deployment can see it.

Reading the log back is Phase 7's job.  ``read`` is that reader: the shape it
returns is the one the dashboard's audit view consumes, and pagination is keyset
(``before`` an id) rather than offset, because the log only ever grows and an
offset would drift under an active guild.
"""

import json

from sqlalchemy import desc, insert, select

from zephyr.db.models import AuditLog
from zephyr.db.session import get_engine

# Keeps one oversized payload from becoming an unbounded row.  JSON columns have
# no length limit, and a queue snapshot or an error blob can be large.
MAX_PAYLOAD_CHARS = 4000

# A page size the UI can render without virtualisation, and a hard ceiling so a
# hand-crafted ?limit= cannot ask for the whole table in one query.
DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def record(
    action: str,
    *,
    actor_id: str,
    guild_id: str | None = None,
    payload: dict | None = None,
    source: str = "web",
    database_url: str | None = None,
) -> None:
    """Write one audit row.  Never raises."""
    try:
        trimmed = _trim(payload)
        engine = get_engine(database_url)
        with engine.begin() as connection:
            connection.execute(
                insert(AuditLog).values(
                    guild_id=str(guild_id) if guild_id else None,
                    actor_id=str(actor_id),
                    action=action,
                    payload=trimmed,
                    source=source,
                )
            )
    except Exception as exc:
        print(f"[Audit] Could not record {action!r}: {exc}")


def read(
    guild_id: str,
    *,
    limit: int = DEFAULT_LIMIT,
    before_id: int | None = None,
    action: str | None = None,
    actor_id: str | None = None,
    source: str | None = None,
    database_url: str | None = None,
) -> dict:
    """Return one page of a guild's audit log, newest first.

    Unlike ``record`` this is *not* fail-soft: a reader that silently returned an
    empty page on a database error would look like "nothing ever happened", which
    is a worse lie than an error the caller can surface.  One extra row is fetched
    to decide ``next_before`` without a second count query.

    Filtering happens here rather than in the client, and that is the whole point
    of it: the page is keyset-paginated, so a client-side filter can only narrow
    the fifty rows it already has.  Asking "every volume change this month" of a
    filter that runs after pagination means paging through the entire log by
    hand.  ``action`` matches a prefix -- ``player`` selects every ``player.*``
    action -- because the actions are namespaced and the useful question is
    almost always about a family rather than one verb.
    """
    page = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    # Explicit columns and .mappings(): a Core connection over ``select(AuditLog)``
    # yields column values, not ORM instances, so selecting the model would hand
    # back bare ids. This keeps the reader on the Core engine every other db helper
    # already uses rather than opening an ORM Session just to read six columns.
    statement = (
        select(
            AuditLog.id, AuditLog.guild_id, AuditLog.actor_id,
            AuditLog.action, AuditLog.payload, AuditLog.source, AuditLog.created_at,
        )
        .where(AuditLog.guild_id == str(guild_id))
        .order_by(desc(AuditLog.id))
        .limit(page + 1)
    )
    if before_id is not None:
        statement = statement.where(AuditLog.id < int(before_id))
    if action:
        # Prefix, not equality: "player" has to mean every player.* action, or
        # the filter is only usable by someone who already knows the verb names.
        statement = statement.where(AuditLog.action.startswith(str(action)))
    if actor_id:
        statement = statement.where(AuditLog.actor_id == str(actor_id))
    if source:
        statement = statement.where(AuditLog.source == str(source))

    engine = get_engine(database_url)
    with engine.connect() as connection:
        rows = connection.execute(statement).mappings().all()

    has_more = len(rows) > page
    entries = [_serialise(row) for row in rows[:page]]
    return {
        "entries": entries,
        "next_before": entries[-1]["id"] if has_more and entries else None,
    }


def _serialise(row) -> dict:
    created = row["created_at"]
    return {
        "id": row["id"],
        "guild_id": row["guild_id"],
        "actor_id": row["actor_id"],
        "action": row["action"],
        "payload": row["payload"],
        "source": row["source"],
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }


def _trim(payload: dict | None) -> dict | None:
    if not payload:
        return None
    encoded = json.dumps(payload, default=str)
    if len(encoded) <= MAX_PAYLOAD_CHARS:
        return payload
    return {"truncated": True, "preview": encoded[:MAX_PAYLOAD_CHARS]}
