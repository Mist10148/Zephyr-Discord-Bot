"""Every registered slash command is documented, and nothing documented is missing.

/help, the ⌘K palette and GET /api/v1/commands all render from help_data, so a
command missing here is invisible everywhere.
"""

import re
from pathlib import Path

from zephyr.utils.help_data import HELP_CATEGORIES

COGS = Path(__file__).resolve().parents[1] / "zephyr" / "cogs"


def _registered():
    names = set()
    for path in COGS.glob("*.py"):
        names.update(re.findall(r"app_commands\.command\(\s*name\s*=\s*[\"']([^\"']+)", path.read_text(encoding="utf-8")))
    return names


def _documented():
    names = set()
    for category in HELP_CATEGORIES:
        for entry in category.commands:
            head = entry.name.split("<", 1)[0].split("[", 1)[0]
            names.update(alias.strip().lstrip("/") for alias in head.split("  /  "))
    return names


def test_every_slash_command_is_in_the_help():
    assert sorted(_registered() - _documented()) == []


def test_the_help_lists_no_command_that_does_not_exist():
    assert sorted(_documented() - _registered()) == []
