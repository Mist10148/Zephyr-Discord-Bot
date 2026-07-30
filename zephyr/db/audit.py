"""Append-only audit trail for mutating actions.

Deliberately fail-soft, and the only module here that is: an audit write must
never be the reason a settings change or a skip fails.  The action itself has
already happened by the time this is called, so raising would report a success as
a failure and invite the user to repeat it.  Losing a log line is the cheaper
failure, and it is logged to stderr where a deployment can see it.

Reading the log back is Phase 7's job; there is deliberately no query helper here
yet, because its shape depends on the UI that will consume it.
"""

import json

from sqlalchemy import insert

from zephyr.db.models import AuditLog
from zephyr.db.session import get_engine

# Keeps one oversized payload from becoming an unbounded row.  JSON columns have
# no length limit, and a queue snapshot or an error blob can be large.
MAX_PAYLOAD_CHARS = 4000


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


def _trim(payload: dict | None) -> dict | None:
    if not payload:
        return None
    encoded = json.dumps(payload, default=str)
    if len(encoded) <= MAX_PAYLOAD_CHARS:
        return payload
    return {"truncated": True, "preview": encoded[:MAX_PAYLOAD_CHARS]}
