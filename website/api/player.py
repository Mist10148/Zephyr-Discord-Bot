"""The web music remote.

``GET`` reads the snapshot the bot publishes; ``POST`` sends a command and waits
for the bot's answer.  Nothing here decides whether the caller may do anything:
the session check is UX, and the bot re-validates the actor against its live
Discord cache before acting.  A refusal comes back as an error from the bridge
and is passed through verbatim, because the bot's reason is the true one.
"""

from flask import current_app, g, jsonify, request

from website.api import api, error
from website.api.guard import guild_scoped, rate_limit
from zephyr.db import audit
from zephyr.services import bridge
from zephyr.core.logging import get_logger


log = get_logger(__name__)
# Actions the browser may send, and whether they take arguments.  A whitelist,
# not a passthrough: the bridge is a general command channel, and a session
# should not be able to reach an action the UI never offers.
PLAYER_ACTIONS = {
    "play": {"query", "mode"},
    "pause": set(),
    "resume": set(),
    "skip": set(),
    "stop": set(),
    "clear": set(),
    "shuffle": set(),
    "seek": {"position"},
    "volume": {"volume"},
    "loop": {"mode"},
    "jump": {"index"},
    "remove": {"index"},
    "move": {"from", "to"},
    "effects": {"reset", "pitch", "bass_boost", "nightcore", "vaporwave", "reverb", "slowed", "slownrev", "sixteen_d"},
    "autoplay": {"enabled"},
}

# Enough for a burst of skips and a slider being dragged, low enough that a
# session cannot use the dashboard to hammer the bot.
PLAYER_RATE_LIMIT = 30
PLAYER_RATE_WINDOW = 10


def bridge_call(action: str, *, guild_id=None, actor_id=None, args=None, timeout=None):
    """Send one bridge command and turn its outcome into an HTTP response.

    The three failure modes are genuinely different and must not be flattened:
    Redis missing is a deployment problem (503), no answer means the bot is not
    running (504), and a refusal is the bot exercising its authority over
    permissions and player state (409) -- retrying that would never help.
    """
    redis_url = current_app.config["REDIS_URL"]
    if not redis_url:
        return error("bridge_not_configured", "This deployment has no Redis, so the bot cannot be reached.", 503)
    kwargs = {"guild_id": guild_id, "actor_id": actor_id, "args": args or {}, "url": redis_url}
    if timeout is not None:
        kwargs["timeout"] = timeout
    try:
        return jsonify(bridge.send_command(action, **kwargs))
    except bridge.BridgeTimeout as exc:
        return error("bot_unreachable", str(exc), 504)
    except bridge.BridgeError as exc:
        return error("bot_refused", str(exc), 409)
    except Exception as exc:
        log.exception("Bridge call %s failed", action)
        return error("bridge_unavailable", "Could not reach Zephyr.", 503)


@api.get("/guilds/<guild_id>/player")
@guild_scoped
def get_player(guild_id: str):
    """The published snapshot, or an explicit "nothing is playing".

    Read straight from Redis rather than asked over the bridge: this is polled
    every few seconds by every open dashboard, and a round trip through the bot
    for each poll would put the player's own event loop on the critical path of
    a page refresh.
    """
    redis_url = current_app.config["REDIS_URL"]
    if not redis_url:
        return error("bridge_not_configured", "This deployment has no Redis, so the bot cannot be reached.", 503)
    try:
        snapshot = bridge.read_player_snapshot(guild_id, url=redis_url)
    except Exception as exc:
        log.exception("Could not read the player snapshot for %s", guild_id)
        return error("bridge_unavailable", "Could not reach Zephyr.", 503)

    if snapshot is None:
        # Absent means the bot is not publishing: it is offline, or it is not in
        # a voice channel here.  Both are "nothing is playing", and neither is an
        # error -- but `live: false` keeps them distinguishable from a real
        # snapshot that happens to be idle.
        return jsonify({"guild_id": guild_id, "live": False, "connected": False, "track": None, "queue": []})
    return jsonify({**snapshot, "live": True})


@api.post("/guilds/<guild_id>/player/<action>")
@guild_scoped
def post_player_action(guild_id: str, action: str):
    allowed = PLAYER_ACTIONS.get(action)
    if allowed is None:
        return error("unknown_action", f"There is no player action called {action!r}.", 400)

    body = request.get_json(silent=True) or {}
    if not isinstance(body, dict):
        return error("invalid_body", "Send a JSON object.", 400)
    unknown = sorted(set(body) - allowed)
    if unknown:
        return error("unknown_fields", f"Cannot set: {', '.join(unknown)}.", 400, unknown)

    if not rate_limit("player", limit=PLAYER_RATE_LIMIT, window=PLAYER_RATE_WINDOW):
        return error("rate_limited", "Slow down a moment.", 429)

    actor_id = g.zephyr_session.user_id
    response = bridge_call(f"player.{action}", guild_id=guild_id, actor_id=actor_id, args=body)

    # Only successes are audited.  A refusal is not a thing that happened to the
    # guild, and logging every rejected button press would drown the log in
    # noise from people who simply were not in the voice channel.
    status = response[1] if isinstance(response, tuple) else 200
    if status == 200:
        audit.record(
            f"player.{action}",
            actor_id=actor_id,
            guild_id=guild_id,
            payload=body or None,
            source="web",
            database_url=current_app.config["DATABASE_URL"],
        )
    return response
