"""GET /guilds/<id> -- the read-only per-guild overview.

Phase 3 only reads. The plan's GET/PATCH /guilds/:id/settings resource lands with
the first editable settings, and keeping this at /guilds/<id> means that arrives
without a rename or a shadowed route.
"""

from flask import current_app, jsonify

from website import discord_api
from website.api import api, error
from website.api.guard import current_session, require_session
from website.repo import read_guild_settings
from zephyr.services import bridge

# Applied when a guild has no row yet, which is the normal state until somebody
# saves settings.  Reported with defaults_applied so the UI can say so.
DEFAULT_SETTINGS = {
    "prefix": "/",
    "locale": "en",
    "timezone": "UTC",
    "default_volume": 50,
    "dj_role_id": None,
    "music_channel_ids": [],
}


@api.get("/guilds/<guild_id>")
@require_session
def guild_overview(guild_id: str):
    if not guild_id.isdigit():
        return error("invalid_guild_id", "That is not a Discord guild id.", 400)

    # Already loaded and cached on g by require_session.
    session = current_session()

    # A UX check, not authorization: the plan is explicit that the bot
    # re-validates the actor's permissions against its live cache before executing
    # anything. Nothing here mutates, so a stale session can only over-restrict.
    if guild_id not in session.manageable_ids():
        return error("forbidden", "You do not manage that server.", 403)

    guild = next(g for g in session.guilds if str(g["id"]) == guild_id)

    try:
        snapshot, snapshot_at = bridge.read_guild_snapshot(url=current_app.config["REDIS_URL"])
    except Exception as exc:
        print(f"[Guilds] Could not read the guild snapshot: {exc}")
        snapshot, snapshot_at = None, None
    bot_present = None if snapshot is None else guild_id in snapshot

    stored = read_guild_settings(guild_id, database_url=current_app.config["DATABASE_URL"])
    settings = dict(DEFAULT_SETTINGS)
    settings["enabled_cogs"] = list(current_app.config["ENABLED_COGS"])
    if stored:
        for key, value in stored.items():
            if key != "id" and value is not None:
                settings[key] = value

    payload = {
        "id": guild_id,
        "name": guild.get("name") or "",
        "icon": guild.get("icon"),
        "icon_url": discord_api.guild_icon_url(guild),
        "owner": bool(guild.get("owner")),
        "bot_present": bot_present,
        "bot_snapshot_at": snapshot_at,
        "defaults_applied": stored is None,
        "editable": False,
        **settings,
    }
    return jsonify(payload)
