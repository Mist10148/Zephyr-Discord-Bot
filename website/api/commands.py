"""Command-registry and public status endpoints."""

import hashlib
import json

from flask import current_app, jsonify

from website import discord_api
from website.api import api
from website.api.guard import public_rate_limit
from zephyr.services import bridge
from zephyr.utils.help_data import HELP_CATEGORIES
from zephyr.core.logging import get_logger



log = get_logger(__name__)
def _parse(command: str) -> tuple[list[str], list[dict]]:
    head = command.split("<", 1)[0].split("[", 1)[0].strip()
    aliases = [part.strip() for part in head.split("  /  ")]
    tail = command[len(head):]
    args = []
    for token in tail.replace("]", "] ").replace(">", "> ").split():
        if token.startswith("<") and token.endswith(">"):
            args.append({"name": token[1:-1], "required": True})
        if token.startswith("[") and token.endswith("]"):
            args.append({"name": token[1:-1], "required": False})
    return aliases, args


@api.get("/commands")
@public_rate_limit("commands", limit=60, window=60)
def commands():
    """The command reference, from the bot's tree when it has published one.

    Two sources, in order.  The bot publishes the list derived from
    ``bot.tree`` at startup, and that is authoritative: it is what Discord was
    actually told.  The hand-maintained ``HELP_CATEGORIES`` is the fallback,
    for a deployment with no Redis and for the window before the bot has ever
    started -- a public reference that answered 503 because the bot is
    restarting would be worse than one that is a deploy behind.

    ``source`` is reported so a reader can tell which they got.  Without it, a
    stale fallback and a fresh derivation are indistinguishable, and "the
    website is missing a command" has two very different causes.
    """
    published = None
    try:
        published = bridge.read_commands(url=current_app.config.get("REDIS_URL"))
    except Exception:
        # A reference is worth serving stale. This is the one endpoint that
        # must answer without the bot.
        log.warning("Could not read the published command list", exc_info=True)

    if published and published.get("commands"):
        payload = {
            "version": published.get("version") or "",
            "count": published.get("count") or len(published["commands"]),
            "commands": published["commands"],
            "categories": published.get("categories") or _fallback_categories(),
            "source": "tree",
        }
    else:
        entries = _fallback_entries()
        payload = {
            "version": hashlib.sha256(
                json.dumps(entries, sort_keys=True).encode()
            ).hexdigest()[:12],
            "count": len(entries),
            "commands": entries,
            "categories": _fallback_categories(),
            "source": "help_data",
        }

    response = jsonify(payload)
    response.set_etag(payload["version"])
    return response


def _fallback_entries() -> list[dict]:
    entries, seen = [], set()
    for category in HELP_CATEGORIES:
        for command in category.commands:
            aliases, args = _parse(command.name)
            name = aliases[0]
            if name in seen:
                continue
            seen.add(name)
            entries.append({
                "name": name, "aliases": aliases[1:], "args": args,
                "description": command.value, "category": category.key,
                "category_title": category.title, "emoji": category.emoji,
            })
    return entries


def _fallback_categories() -> list[dict]:
    return [
        {"key": category.key, "title": category.title, "emoji": category.emoji}
        for category in HELP_CATEGORIES
    ]


@api.get("/site")
@public_rate_limit("site", limit=60, window=60)
def site():
    """The links the footer offers.

    Served rather than baked into the bundle so a fork can point them at its
    own support server and repository without rebuilding the frontend -- and so
    a deployment with neither renders no dead links.
    """
    return jsonify({
        "support_url": current_app.config.get("SUPPORT_URL") or None,
        "repository_url": current_app.config.get("REPOSITORY_URL") or None,
    })


@api.get("/legal")
@public_rate_limit("legal", limit=60, window=60)
def legal():
    """The retention table /privacy renders, served from the code that
    implements it.

    Deliberately not duplicated into the frontend. Discord requires a privacy
    policy for verification, and a policy that describes a deletion path has to
    match the path that actually runs -- so the table lives beside
    ``personal_data.delete`` and the page reads it, rather than the two being
    edited independently and drifting.
    """
    from zephyr.db.personal_data import RETENTION, SESSION_CAVEAT

    return jsonify({
        "retention": [{"category": key, "detail": value} for key, value in RETENTION.items()],
        "session_caveat": SESSION_CAVEAT,
        "contact": current_app.config.get("SUPPORT_URL") or None,
        "deletion": {
            "self_service": ["/export-my-data", "/delete-my-data"],
            "per_channel": ["/forget"],
        },
    })


@api.get("/status")
@public_rate_limit("status", limit=120, window=60)
def status():
    """Public liveness, read from the bot's heartbeat.

    Absent means offline, and it means it within 30 seconds: the presence key's
    TTL is the signal, so there is no stale-but-present state to disambiguate.
    A deployment with no Redis reports offline too -- which is true, in the only
    sense the web tier can observe.
    """
    redis_url = current_app.config["REDIS_URL"]
    presence = None
    if redis_url:
        try:
            presence = bridge.read_presence(url=redis_url)
        except Exception as exc:
            log.exception("Could not read presence")

    if not presence or not presence.get("online"):
        return jsonify({
            "bot": {"online": False, "guild_count": None, "latency_ms": None,
                    "uptime_s": None, "published_at": (presence or {}).get("published_at")},
            "invite_url": _invite_url(),
        })
    return jsonify(
        {
            "bot": {
                "online": True,
                "guild_count": presence.get("guild_count"),
                "latency_ms": presence.get("latency_ms"),
                "uptime_s": presence.get("uptime_s"),
                "published_at": presence.get("published_at"),
                "shard_count": presence.get("shard_count"),
            },
            # Unauthenticated on purpose: this is the primary conversion action
            # for a bot's website, and it was only reachable from GET /me --
            # behind a sign-in. Somebody landing on / was offered "check the
            # weather" and no way to install the thing.
            "invite_url": _invite_url(),
        }
    )


def _invite_url() -> str | None:
    """Derived exactly as GET /me does, from the same two config values.

    None when DISCORD_CLIENT_ID is unset, which is the weather-only deployment
    -- the frontend then simply does not render the button rather than linking
    to a broken authorize URL.
    """
    client_id = current_app.config.get("DISCORD_CLIENT_ID")
    if not client_id:
        return None
    return discord_api.invite_url(
        client_id=client_id,
        permissions=current_app.config["DISCORD_INVITE_PERMISSIONS"],
    )
