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


# ---------------------------------------------------------------------------
# Arguments, order and rendering
# ---------------------------------------------------------------------------
import importlib
import inspect

import discord
import pytest

from zephyr.utils import help_data


def _real_signatures():
    """``{command name: "<required> [optional]"}`` from the cogs themselves."""
    signatures = {}
    for module in ("weather", "weather_alerts", "music", "voice_tts", "chat", "help"):
        loaded = importlib.import_module(f"zephyr.cogs.{module}")
        for value in vars(loaded).values():
            if inspect.isclass(value) and hasattr(value, "__cog_app_commands__"):
                for command in value.__cog_app_commands__:
                    signatures[command.name] = " ".join(
                        (f"<{p.name}>" if p.required else f"[{p.name}]") for p in command.parameters)
    return signatures


def test_every_argument_hint_matches_the_real_parameters():
    """/help said <query> for /play's `search`, <time> for /seek's `position`..."""
    real = _real_signatures()
    wrong = []
    for category in HELP_CATEGORIES:
        for entry in category.commands:
            first_arg = min((i for i in (entry.name.find(" <"), entry.name.find(" [")) if i >= 0), default=len(entry.name))
            aliases, hint = entry.name[:first_arg], entry.name[first_arg:].strip()
            for alias in aliases.split("  /  "):
                name = alias.strip().lstrip("/")
                if real.get(name, hint) != hint:
                    wrong.append(f"/{name}: help says {hint!r}, command takes {real[name]!r}")
    assert wrong == []


def test_every_command_is_listed_exactly_once():
    names = [entry.name for category in HELP_CATEGORIES for entry in category.commands]
    assert len(names) == len(set(names))


def test_categories_run_weather_music_voice_ai_utility():
    keys = [category.key for category in HELP_CATEGORIES]
    groups = [key.split("_")[0] if key.startswith(("weather", "music")) else key for key in keys]
    order = []
    for group in groups:
        if not order or order[-1] != group:
            order.append(group)
    assert order == ["weather", "music", "tts", "chat", "utility", "help"]


@pytest.mark.parametrize("keys", [
    tuple(category.key for category in HELP_CATEGORIES),
    help_data.WEATHER_KEYS, help_data.MUSIC_KEYS, help_data.CHAT_KEYS,
])
def test_every_help_page_fits_discord(keys):
    pages = help_data._category_embeds(help_data.categories_by_key(*keys), "Zephyr Commands",
                                       discord.Color.blurple(), include_toc=True)
    assert pages
    for page in pages:
        assert len(page) <= 6000 and len(page.description or "") <= 4096
    assert len(help_data.text_summary(help_data.WEATHER_KEYS)) <= 4096
