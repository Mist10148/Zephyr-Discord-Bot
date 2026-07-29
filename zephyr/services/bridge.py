"""Bot -> web guild membership snapshot.

The web process has no Discord gateway connection, so it cannot know which guilds
the bot is actually in.  The bot publishes a snapshot to Redis and the dashboard
reads it.

This is *not* the Phase 4 bot/web bridge: there is no pub/sub, no command channel
and no response correlation here, only two plain reads and writes.  Phase 4
extends this module with the rest.

No ``import discord`` -- callers pass plain dicts -- so the web tier can import
this without pulling the gateway library into a Flask worker.
"""

import json
import time

from zephyr.services.redis_client import get_client

GUILDS_KEY = "zephyr:guilds"
GUILDS_UPDATED_KEY = "zephyr:guilds:updated_at"


def write_guild_snapshot(guilds: list[dict], *, url: str | None = None) -> None:
    """Replace the published snapshot with ``guilds``.

    Each entry should be ``{"id": str, "name": str, "icon": str | None}``.

    The key has no TTL, unlike a presence key.  Membership changes rarely, and
    expiring it while the bot is briefly down would make every server vanish from
    the picker -- worse than serving a snapshot that is a few minutes stale.  Every
    bot start rewrites it, which bounds the staleness in practice, and
    ``zephyr:guilds:updated_at`` exposes that bound to the UI.
    """
    client = get_client(url)
    payload = {str(guild["id"]): guild for guild in guilds}
    pipeline = client.pipeline()
    pipeline.set(GUILDS_KEY, json.dumps(payload))
    pipeline.set(GUILDS_UPDATED_KEY, str(int(time.time())))
    pipeline.execute()


def read_guild_snapshot(*, url: str | None = None) -> tuple[dict[str, dict] | None, int | None]:
    """Return ``(guilds_by_id, updated_at)``, or ``(None, None)`` when unpublished.

    ``None`` means "the bot has never published" -- a distinct state from "the bot
    is in no guilds", and the caller must not present it as an empty list.
    """
    client = get_client(url)
    raw, updated = client.mget(GUILDS_KEY, GUILDS_UPDATED_KEY)
    if not raw:
        return None, None
    try:
        guilds = json.loads(raw)
    except (TypeError, ValueError):
        return None, None
    if not isinstance(guilds, dict):
        return None, None
    try:
        stamp = int(updated) if updated else None
    except (TypeError, ValueError):
        stamp = None
    return guilds, stamp
