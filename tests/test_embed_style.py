"""Guards for `docs/BOT_OUTPUT.md`.

Phase 16 turned 130 hand-built embeds in eleven colours into six roles from one
factory, and applied one stated ephemeral rule. Both are the kind of thing that
degrades one commit at a time: somebody in a hurry writes
`discord.Embed(color=discord.Color.green())` because it is two fewer characters
to think about, and a year later there are eleven colours again.

These are AST walks rather than greps, so a colour named inside a string or a
comment does not trip them, and each has a self-check proving the walk can
actually see an offender -- otherwise the guards pass because the walk found
nothing.
"""

import ast
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

# The factory itself is where `discord.Embed` and the palette live.
EXEMPT_FILES = {"zephyr/utils/embeds.py"}


def _python_files(root: pathlib.Path):
    for file in sorted(root.rglob("*.py")):
        if "__pycache__" in file.parts or "migrations" in file.parts:
            continue
        relative = file.relative_to(PROJECT_ROOT).as_posix()
        if relative in EXEMPT_FILES:
            continue
        yield relative, file


def _is_embed_construction(node: ast.AST) -> bool:
    """`discord.Embed(...)` or a bare `Embed(...)`.

    Both forms matter: `weather.py` imports `Embed` directly, and its whole
    slash-command half was invisible to a search for `discord.Embed(` -- which
    is how six raw hex colours survived the first count.
    """
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "Embed":
        return True
    return isinstance(func, ast.Name) and func.id == "Embed"


def _names_a_colour(node: ast.AST) -> bool:
    """`discord.Color.anything` or `discord.Colour.anything`."""
    if not isinstance(node, ast.Attribute):
        return False
    value = node.value
    return (
        isinstance(value, ast.Attribute)
        and value.attr in {"Color", "Colour"}
        and isinstance(value.value, ast.Name)
        and value.value.id == "discord"
    )


class TestNoCogBuildsItsOwnEmbed:
    """`docs/BOT_OUTPUT.md` §1: every embed comes from the factory."""

    def _offenders(self, root: pathlib.Path):
        found = []
        for relative, file in _python_files(root):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if _is_embed_construction(node):
                    found.append(f"{relative}:{node.lineno}")
        return found

    def test_the_bot_package_is_clean(self):
        assert self._offenders(PROJECT_ROOT / "zephyr") == []

    def test_the_guard_can_see_a_construction(self):
        """Otherwise the test above passes because the walk found nothing."""
        tree = ast.parse("import discord\ne = discord.Embed(title='x')\n")
        assert any(_is_embed_construction(node) for node in ast.walk(tree))

    def test_the_guard_sees_the_bare_import_form_too(self):
        """`from discord import Embed` is how 27 sites hid from the first
        count."""
        tree = ast.parse("from discord import Embed\ne = Embed(title='x')\n")
        assert any(_is_embed_construction(node) for node in ast.walk(tree))


class TestNoCogNamesAColour:
    """§1 again: a cog picks a *role*, and the factory owns the colour."""

    def _offenders(self, root: pathlib.Path):
        found = []
        for relative, file in _python_files(root):
            tree = ast.parse(file.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if _names_a_colour(node):
                    found.append(f"{relative}:{node.lineno}")
        return found

    def test_the_bot_package_is_clean(self):
        assert self._offenders(PROJECT_ROOT / "zephyr") == []

    def test_the_guard_can_see_a_named_colour(self):
        tree = ast.parse("import discord\nc = discord.Color.green()\n")
        assert any(_names_a_colour(node) for node in ast.walk(tree))

    def test_it_does_not_trip_on_a_colour_in_a_string(self):
        """The reason this is an AST walk and not a grep: `BOT_OUTPUT.md` and
        two docstrings mention `discord.Color.green()` by name."""
        tree = ast.parse("x = 'discord.Color.green()'\n")
        assert not any(_names_a_colour(node) for node in ast.walk(tree))


class TestErrorsAreEphemeral:
    """`docs/BOT_OUTPUT.md` §2, the half that is mechanically checkable.

    A failed `/play` in a busy channel is noise for everybody except the person
    who typed it, and it was the single largest source of channel spam before
    the rule was written down.
    """

    # `_notify` posts to the sticky now-playing channel: there is no
    # interaction to be ephemeral on, and "the next track failed to load" is
    # information the channel needs, because the music just stopped.
    ALLOWED = {("zephyr/cogs/music/__init__.py", "_notify")}

    def _offenders(self, root: pathlib.Path):
        found = []
        for relative, file in _python_files(root):
            source = file.read_text(encoding="utf-8")
            tree = ast.parse(source)
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"send_message", "send", "edit_original_response"}:
                    continue
                if not self._sends_an_error(node):
                    continue
                if (relative, node.func.attr) in self.ALLOWED:
                    continue
                if not any(
                    keyword.arg == "ephemeral" for keyword in node.keywords
                ):
                    found.append(f"{relative}:{node.lineno}")
        return found

    @staticmethod
    def _sends_an_error(call: ast.Call) -> bool:
        for keyword in call.keywords:
            if keyword.arg != "embed":
                continue
            value = keyword.value
            return (
                isinstance(value, ast.Call)
                and isinstance(value.func, ast.Attribute)
                and value.func.attr == "error"
                and isinstance(value.func.value, ast.Name)
                and value.func.value.id == "embeds"
            )
        return False

    def test_the_bot_package_is_clean(self):
        assert self._offenders(PROJECT_ROOT / "zephyr") == []

    def test_the_guard_can_see_a_public_error(self):
        """Otherwise the test above passes because the walk found nothing."""
        tree = ast.parse(
            "await interaction.followup.send(embed=embeds.error('x'))\n",
            mode="exec",
        )
        assert any(
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "send"
            and TestErrorsAreEphemeral._sends_an_error(node)
            and not any(k.arg == "ephemeral" for k in node.keywords)
            for node in ast.walk(tree)
        )

    def test_the_guard_accepts_an_ephemeral_error(self):
        tree = ast.parse(
            "await interaction.followup.send(embed=embeds.error('x'), ephemeral=True)\n"
        )
        assert all(
            any(k.arg == "ephemeral" for k in node.keywords)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "send"
            and TestErrorsAreEphemeral._sends_an_error(node)
        )


class TestTheStyleDocExists:
    def test_it_is_where_the_guards_say_it_is(self):
        """Three guards cite it by path, and a rule nobody can find is a rule
        nobody applies."""
        doc = PROJECT_ROOT / "docs" / "BOT_OUTPUT.md"

        assert doc.exists()
        text = doc.read_text(encoding="utf-8")
        assert "errors and personal settings" in text.lower()

    @pytest.mark.parametrize(
        "role", ["success", "error", "warning", "info", "neutral", "brand"]
    )
    def test_every_role_is_documented(self, role):
        """A role the factory offers and the doc omits is a role somebody will
        use for the wrong thing."""
        from zephyr.utils import embeds

        assert role in embeds.ACCENTS
        assert f"`{role}`" in (PROJECT_ROOT / "docs" / "BOT_OUTPUT.md").read_text(
            encoding="utf-8"
        )
