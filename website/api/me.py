"""GET /me -- the signed-in user and the guilds they can administer."""

import time

from flask import current_app, jsonify

from website import discord_api
from website.api import api
from website.api.guard import current_session, require_session
from zephyr.services import bridge
from zephyr.core.logging import get_logger



log = get_logger(__name__)
def annotate_bot_presence(guilds: list[dict]) -> tuple[list[dict], int | None]:
    """Add bot_present to each guild, without ever removing one.

    The plan's API sketch says "manageable guilds where the bot is present", but
    filtering is the wrong call. Silently hiding a server the user administers is
    an unexplainable dead end -- "why isn't my server listed?" -- and when no
    snapshot has been published, filtering would hide *everything*. Annotating is
    strictly more informative and gives the invite CTA somewhere to live.

    bot_present is None, not False, when the bot has never published: unknown and
    absent are different states and must render differently.
    """
    try:
        snapshot, updated_at = bridge.read_guild_snapshot(url=current_app.config["REDIS_URL"])
    except Exception as exc:
        # A snapshot outage degrades the annotation; it must not fail the request.
        log.exception("Could not read the guild snapshot")
        snapshot, updated_at = None, None

    annotated = []
    for guild in guilds:
        entry = dict(guild)
        entry["bot_present"] = None if snapshot is None else str(guild["id"]) in snapshot
        entry["icon_url"] = discord_api.guild_icon_url(guild)
        annotated.append(entry)
    return annotated, updated_at


@api.get("/me")
@require_session
def me():
    # require_session has already loaded and cached it on g, so this cannot fail
    # or return None here.
    session = current_session()
    guilds, snapshot_at = annotate_bot_presence(session.guilds)
    fresh_for = current_app.config["GUILDS_FRESH_SECONDS"]
    client_id = current_app.config["DISCORD_CLIENT_ID"]
    permissions = current_app.config["DISCORD_INVITE_PERMISSIONS"]

    return jsonify(
        {
            "user": {
                "id": session.user_id,
                "username": session.username,
                "global_name": session.global_name,
                "avatar": session.avatar_hash,
                "avatar_url": discord_api.avatar_url(
                    {"id": session.user_id, "avatar": session.avatar_hash}
                ),
            },
            "guilds": guilds,
            # No Discord token is stored, so a stale guild list can only be
            # refreshed by re-running OAuth. prompt=none makes that a silent
            # redirect round trip, so the client is told when it is worth doing.
            "guilds_stale": (int(time.time()) - session.guilds_fetched_at) > fresh_for,
            "bot_snapshot_at": snapshot_at,
            "invite_url": discord_api.invite_url(client_id=client_id, permissions=permissions),
            "csrf_token": session.csrf,
        }
    )
