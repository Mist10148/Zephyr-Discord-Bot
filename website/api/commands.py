"""Command-registry and public status endpoints."""

import hashlib
import json

from flask import jsonify

from website.api import api
from zephyr.utils.help_data import HELP_CATEGORIES


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
    return jsonify({"bot": {"online": False}})
