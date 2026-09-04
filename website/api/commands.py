"""Command-registry and public status endpoints."""

import hashlib
import json

from flask import current_app, jsonify

from website.api import api
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
def commands():
    entries, seen = [], set()
    for category in HELP_CATEGORIES:
        for command in category.commands:
            aliases, args = _parse(command.name)
            name = aliases[0]
            if name in seen: continue
            seen.add(name)
            entries.append({"name": name, "aliases": aliases[1:], "args": args, "description": command.value, "category": category.key, "category_title": category.title, "emoji": category.emoji})
    version = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:12]
    response = jsonify({"version": version, "count": len(entries), "commands": entries, "categories": [{"key": category.key, "title": category.title, "emoji": category.emoji} for category in HELP_CATEGORIES]})
    response.set_etag(version)
    return response


@api.get("/status")
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
        return jsonify({"bot": {"online": False, "guild_count": None, "latency_ms": None,
                                "uptime_s": None, "published_at": (presence or {}).get("published_at")}})
    return jsonify(
        {
            "bot": {
                "online": True,
                "guild_count": presence.get("guild_count"),
                "latency_ms": presence.get("latency_ms"),
                "uptime_s": presence.get("uptime_s"),
                "published_at": presence.get("published_at"),
            }
        }
    )
