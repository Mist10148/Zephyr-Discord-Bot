"""The one command list, and the drift it exists to stop.

There were three copies of "which commands exist": `help_data.HELP_CATEGORIES`,
the web reference behind `GET /commands`, and a count in `README.md` and
`docs/PRD.md`. During Phase 15 the docs said **75** while `bot.tree` held
**114**, and nothing anywhere noticed for eight merged pull requests.

`test_the_documented_counts_match_the_tree` is the test that would have. It is
the point of this file; the rest is the machinery it needs.
"""

import pathlib
import re

import discord
import pytest
from discord import app_commands

from zephyr.config import ENABLED_COGS
from zephyr.utils import command_registry
from zephyr.utils.help_data import HELP_CATEGORIES

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _every_cog_command():
    """Every app command every enabled cog defines.

    Built by importing the cogs rather than by starting a bot: constructing a
    real `CommandTree` needs a Client, and what is under test is the set of
    commands the cogs declare -- which is exactly what `setup_hook` puts in the
    tree.
    """
    import importlib

    from discord.ext import commands as ext_commands

    found = []
    for name in ENABLED_COGS:
        module = importlib.import_module(f"zephyr.cogs.{name}")
        for value in vars(module).values():
            # Classes only. Probing every module-level name with `getattr` is
            # what it looks like it should be, and it is not: `gemini`'s lazy
            # client is a forwarding proxy that answers any attribute, so
            # asking it for `__cog_app_commands__` built a real Gemini client
            # and raised in CI, where there is no API key.
            if not isinstance(value, type) or not issubclass(value, ext_commands.Cog):
                continue
            if value.__module__ != module.__name__:
                continue
            found.extend(getattr(value, "__cog_app_commands__", ()))
    return found


class _FakeTree:
    """The two methods `command_registry` uses, over a list of commands."""

    def __init__(self, commands):
        self._commands = list(commands)

    def walk_commands(self):
        return iter(self._commands)


@pytest.fixture(scope="module")
def tree():
    return _FakeTree(_every_cog_command())


# ---------------------------------------------------------------------------
# The drift guard
# ---------------------------------------------------------------------------


# The four places the current count is stated. Deliberately specific rather
# than "any number before the words slash commands": PRD.md's changelog says
# "64 slash commands" about version 1.0, which is a historical fact and must
# not be rewritten every time a command is added.
COUNT_PATTERNS = (
    r"\*\*(\d+) slash commands\*\*",
    r"Slash commands \((\d+) total",
    r"\*\*Slash commands:\*\* (\d+)",
)


def _documented_counts() -> dict[str, list[int]]:
    """Every *current* slash-command count written in the docs, by file."""
    counts: dict[str, list[int]] = {}
    for relative in ("README.md", "docs/PRD.md"):
        text = (PROJECT_ROOT / relative).read_text(encoding="utf-8")
        found = []
        for pattern in COUNT_PATTERNS:
            found.extend(int(match) for match in re.findall(pattern, text))
        counts[relative] = found
    return counts


class TestTheDocumentedCounts:
    def test_the_documented_counts_match_the_tree(self, tree):
        """The test the last eight pull requests needed.

        Each of them added commands and hand-edited two files and a table; this
        is what makes forgetting one a red build rather than a slow lie.
        """
        actual = len(command_registry.tree_names(tree))
        documented = _documented_counts()

        assert documented["README.md"], "README.md states no command count at all"
        assert documented["docs/PRD.md"], "docs/PRD.md states no command count at all"
        for relative, counts in documented.items():
            assert set(counts) == {actual}, (
                f"{relative} says {sorted(set(counts))}; the tree holds {actual}"
            )

    def test_the_count_extractor_actually_finds_numbers(self):
        """Otherwise the test above passes because the regex matched nothing."""
        counts = _documented_counts()

        assert len(counts["README.md"]) >= 1
        assert len(counts["docs/PRD.md"]) >= 3

    def test_the_changelog_is_not_rewritten(self):
        """PRD.md's changelog says version 1.0 had 64 slash commands, which is
        a historical fact -- an extractor that matched it would demand the
        history be edited every time a command is added."""
        assert 64 not in _documented_counts()["docs/PRD.md"]


# ---------------------------------------------------------------------------
# Derivation
# ---------------------------------------------------------------------------


class TestTheLazyClientIsNotProbeable:
    """A regression guard for what this file's own fixture tripped.

    `gemini`'s lazy client is a forwarding proxy, so before this it answered
    *any* attribute by constructing a real Gemini client. A test scanning a
    module's names for cogs therefore opened a network client and raised in CI,
    where there is no API key -- and so would `copy`, `pickle`, or any
    duck-typing `hasattr` in the ecosystem.
    """

    def test_a_dunder_probe_does_not_build_a_client(self):
        from zephyr.services import gemini

        assert not hasattr(gemini.gemini_async_client, "__cog_app_commands__")
        assert gemini._client is None

    def test_a_real_attribute_still_forwards(self, monkeypatch):
        """The guard must not break the proxy it is protecting."""
        from types import SimpleNamespace

        from zephyr.services import gemini

        monkeypatch.setattr(gemini, "get_gemini_client", lambda: SimpleNamespace(models="M"))
        proxy = gemini._LazyClient(gemini.get_gemini_client)

        assert proxy.models == "M"


class TestTreeNames:
    def test_it_finds_every_command(self, tree):
        names = command_registry.tree_names(tree)

        assert "play" in names
        assert "remindme" in names
        assert "tag" in names

    def test_it_is_sorted_and_unique(self, tree):
        names = command_registry.tree_names(tree)

        assert names == sorted(set(names))

    def test_it_walks_subcommands_not_just_groups(self):
        """There are no groups today, and a list that stopped at the group
        would be wrong the moment there is one."""
        import inspect

        assert "walk_commands" in inspect.getsource(command_registry.tree_names)


class TestDescribe:
    def test_every_tree_command_is_described(self, tree):
        described = {entry["name"] for entry in command_registry.describe(tree)}
        names = set(command_registry.tree_names(tree))
        aliases = {
            alias
            for data in command_registry.prose_index().values()
            for alias in data["aliases"]
        }

        assert described == names - aliases

    def test_an_alias_is_not_a_command_of_its_own(self, tree):
        """`/np` is `/now` under another name. Listing it separately would
        report two commands where Discord shows one entry with two names."""
        entries = {entry["name"]: entry for entry in command_registry.describe(tree)}

        assert "np" not in entries
        assert "np" in entries["now"]["aliases"]

    def test_the_prose_comes_from_the_help_data(self, tree):
        entries = {entry["name"]: entry for entry in command_registry.describe(tree)}

        assert entries["play"]["description"] == "Play a song from YouTube or Spotify"

    def test_a_command_with_no_help_entry_still_appears(self):
        """A command Discord accepts and the reference denies exists is the
        worse failure of the two, so a missing help entry degrades to the
        tree's own description rather than dropping the command."""
        fake = app_commands.Command(
            name="undocumented",
            description="From the decorator",
            callback=_noop,
        )
        entries = {
            entry["name"]: entry for entry in command_registry.describe(_FakeTree([fake]))
        }

        assert entries["undocumented"]["description"] == "From the decorator"
        assert entries["undocumented"]["category"] == command_registry.UNCATEGORISED

    def test_arguments_are_parsed_from_the_prose(self, tree):
        """The tree knows the real parameters and is deliberately not used: the
        help entries name arguments the way a person says them out loud, which
        is the point of a reference."""
        entries = {entry["name"]: entry for entry in command_registry.describe(tree)}

        assert entries["play"]["args"] == [{"name": "query", "required": True}]

    def test_an_optional_argument_is_marked_optional(self, tree):
        entries = {entry["name"]: entry for entry in command_registry.describe(tree)}

        assert {"name": "member", "required": False} in entries["rank"]["args"]


class TestTheTwoListsAgree:
    def test_no_help_entry_names_a_command_that_does_not_exist(self, tree):
        """The drift that actually happened, in the direction that matters
        least but is easiest to check: a help entry for a command that was
        renamed or removed."""
        documented = set(command_registry.prose_index())
        aliases = {
            alias
            for data in command_registry.prose_index().values()
            for alias in data["aliases"]
        }
        real = set(command_registry.tree_names(tree))
        # The prefix commands are documented in HELP_CATEGORIES too and are not
        # app commands, so they are excluded by name.
        prefix_only = {"ping"} & documented
        missing = documented - real - aliases - prefix_only

        assert missing == set(), f"documented but not in the tree: {sorted(missing)}"

    def test_no_command_is_left_uncategorised(self, tree):
        """UNCATEGORISED exists so a missing help entry cannot drop a command,
        not as a place for commands to live."""
        entries = command_registry.describe(tree)
        orphans = [
            entry["name"]
            for entry in entries
            if entry["category"] == command_registry.UNCATEGORISED
        ]

        assert orphans == [], f"no help entry for: {sorted(orphans)}"

    def test_every_category_has_at_least_one_command(self):
        """An empty category renders as an empty help page."""
        empty = [category.key for category in HELP_CATEGORIES if not category.commands]

        assert empty == []


class TestThePayload:
    def test_it_carries_a_version(self, tree):
        payload = command_registry.payload(tree)

        assert payload["version"]
        assert payload["count"] == len(payload["commands"])

    def test_the_version_changes_with_the_commands(self, tree):
        first = command_registry.payload(tree)["version"]
        fake = app_commands.Command(name="extra", description="x", callback=_noop)
        second = command_registry.payload(
            _FakeTree(list(tree.walk_commands()) + [fake])
        )["version"]

        assert first != second

    def test_the_version_is_stable_for_the_same_commands(self, tree):
        assert (
            command_registry.payload(tree)["version"]
            == command_registry.payload(tree)["version"]
        )

    def test_it_carries_the_categories(self, tree):
        keys = {category["key"] for category in command_registry.payload(tree)["categories"]}

        assert {"music_playback", "reminders", "moderation", "tags"} <= keys


async def _noop(interaction: discord.Interaction):
    return None
