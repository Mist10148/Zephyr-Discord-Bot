"""The command list, derived from the tree rather than hand-maintained.

There were three copies of "which commands exist": `help_data.HELP_CATEGORIES`
(257 lines), the web reference behind `GET /commands`, and a count in
`README.md` and `docs/PRD.md`. They drifted exactly as you would expect --
during Phase 15 the docs said **75** while `bot.tree` held **114**, and nothing
anywhere noticed.

The split this module makes is the one that actually holds:

**The set of commands is derived.** `bot.tree` is the only thing that knows what
Discord will accept, because it is what Discord was told. Anything else is a
transcription.

**The prose is not derivable and stays hand-written.** "Play a song from YouTube
or Spotify" cannot come from a signature. `HELP_CATEGORIES` keeps that, plus the
category ordering and the aliasing (`/now  /  /np`), which are editorial
decisions rather than facts about the tree.

So `describe` walks the tree for the facts and reads `HELP_CATEGORIES` for the
prose, and `tests/test_command_registry.py` fails when one has an entry the
other does not.
"""

from __future__ import annotations

import hashlib
import json

from zephyr.utils.help_data import HELP_CATEGORIES

# Where a command with no category lands. It should never be used -- a test
# asserts as much -- but a command missing from the help data must still appear
# in the published list, because the alternative is a command Discord accepts
# and the reference denies exists.
UNCATEGORISED = "other"


def prose_index() -> dict[str, dict]:
    """Command name -> its hand-written description and category.

    Keyed on the *first* name in a help entry, so `/now  /  /np` indexes under
    "now" and lists "np" as an alias -- which is how the entry is written and
    how the web reference has always parsed it.
    """
    index: dict[str, dict] = {}
    for category in HELP_CATEGORIES:
        for entry in category.commands:
            names = _names(entry.name)
            if not names:
                continue
            index[names[0]] = {
                "aliases": names[1:],
                "description": entry.value,
                "category": category.key,
                "category_title": category.title,
                "emoji": category.emoji,
                "args": _args(entry.name),
            }
    return index


def tree_names(tree) -> list[str]:
    """Every app command name Discord has been told about.

    ``walk_commands`` rather than ``get_commands``, so a group's subcommands are
    counted -- there are none today, and a list that silently stopped at the
    group would be wrong the moment there are.
    """
    names = []
    for command in tree.walk_commands():
        name = getattr(command, "qualified_name", None) or command.name
        names.append(str(name))
    return sorted(set(names))


def describe(tree) -> list[dict]:
    """The published command list: the tree's facts, the help data's prose."""
    prose = prose_index()
    # Alias -> canonical, so /np does not appear as a command of its own.
    alias_owner = {
        alias: name for name, data in prose.items() for alias in data["aliases"]
    }

    entries = []
    for name in tree_names(tree):
        if name in alias_owner:
            continue
        data = prose.get(name)
        entries.append(
            {
                "name": name,
                "aliases": data["aliases"] if data else [],
                "args": data["args"] if data else [],
                # The tree's own description when the help data has none, so a
                # command added without a help entry is still described rather
                # than blank.
                "description": (data["description"] if data else None)
                or _tree_description(tree, name),
                "category": data["category"] if data else UNCATEGORISED,
                "category_title": data["category_title"] if data else "Other",
                "emoji": data["emoji"] if data else "•",
            }
        )
    return entries


def payload(tree) -> dict:
    """What the bot publishes over the bridge.

    Carries its own version so the web tier can serve an ETag without hashing
    the list on every request, and so a dashboard can tell a changed command set
    from a re-publish of the same one.
    """
    entries = describe(tree)
    return {
        "commands": entries,
        "count": len(entries),
        "categories": [
            {"key": category.key, "title": category.title, "emoji": category.emoji}
            for category in HELP_CATEGORIES
        ],
        "version": version_of(entries),
    }


def version_of(entries: list[dict]) -> str:
    return hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()[:12]


def _names(label: str) -> list[str]:
    """"/now  /  /np" -> ["now", "np"]; "/play <query>" -> ["play"]."""
    head = label.split("<", 1)[0].split("[", 1)[0].strip()
    return [part.strip().lstrip("/") for part in head.split("  /  ") if part.strip()]


def _args(label: str) -> list[dict]:
    """"/play <query> [next]" -> the two arguments, and whether each is required.

    Parsed from the prose because the prose is where it is written. The tree
    knows the real parameters, and deliberately is not used: the help entries
    name arguments the way a person would say them out loud, which is the point
    of a reference.
    """
    head = label.split("<", 1)[0].split("[", 1)[0].strip()
    tail = label[len(head):]
    args = []
    for token in tail.replace("]", "] ").replace(">", "> ").split():
        if token.startswith("<") and token.endswith(">"):
            args.append({"name": token[1:-1], "required": True})
        elif token.startswith("[") and token.endswith("]"):
            args.append({"name": token[1:-1], "required": False})
    return args


def _tree_description(tree, name: str) -> str:
    for command in tree.walk_commands():
        if (getattr(command, "qualified_name", None) or command.name) == name:
            return str(getattr(command, "description", "") or "")
    return ""
