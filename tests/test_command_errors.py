"""The slash-command error handler.

Nothing handled these before: ZephyrBot registered no on_app_command_error and
no tree.on_error, and the only hook in the package was the prefix one. An
unhandled exception in any of the 75 slash commands produced "The application
did not respond" and nothing in any log.

Interactions are hand-stubbed with MagicMock, per the convention in
tests/test_music_now_playing.py -- there is no dpytest here and the gateway is
never driven.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from discord import app_commands
from discord.ext import commands

from zephyr.core import errors


def _interaction(*, done=False, command="play"):
    interaction = MagicMock(spec=discord.Interaction)
    interaction.response = MagicMock()
    interaction.response.is_done.return_value = done
    interaction.response.send_message = AsyncMock()
    interaction.followup = MagicMock()
    interaction.followup.send = AsyncMock()
    interaction.command = MagicMock()
    interaction.command.qualified_name = command
    interaction.guild_id = 1
    interaction.user = MagicMock()
    interaction.user.id = 900000000000000001
    return interaction


class _FakeCooldown:
    def __init__(self, retry_after):
        self.retry_after = retry_after


class TestUserFaultMessages:
    """Something the person can act on: a plain sentence, and no log noise."""

    def test_a_cooldown_says_how_long(self):
        error = app_commands.CommandOnCooldown(_FakeCooldown(7.4), 7.4)
        assert errors.user_facing_message(error) == "That command is on cooldown — try again in 7s."

    def test_a_missing_permission_names_it_readably(self):
        error = app_commands.MissingPermissions(["manage_guild"])
        assert errors.user_facing_message(error) == "You need the manage guild permission to do that."

    def test_the_bot_missing_a_permission_is_a_different_sentence(self):
        # "You need..." would send someone to change their own permissions.
        error = app_commands.BotMissingPermissions(["send_messages"])
        assert "I need the send messages permission" in errors.user_facing_message(error)

    def test_a_specific_check_failure_beats_the_generic_one(self):
        """MissingPermissions *is* a CheckFailure, so order matters: matching
        the base class first would throw away the useful detail."""
        specific = errors.user_facing_message(app_commands.MissingPermissions(["manage_messages"]))
        generic = errors.user_facing_message(app_commands.CheckFailure("nope"))
        assert specific != generic
        assert "manage messages" in specific

    def test_a_bare_check_failure_still_says_something(self):
        # This is the case chat.py's /forget comment complained about.
        assert errors.user_facing_message(app_commands.CheckFailure("x")) == "You cannot use that command here."

    def test_a_dm_only_failure_explains_the_restriction(self):
        assert "inside a server" in errors.user_facing_message(app_commands.NoPrivateMessage())

    def test_zephyrs_own_refusals_pass_straight_through(self):
        """VoiceError and friends are already written for users."""
        from zephyr.cogs.music import VoiceError

        assert errors.user_facing_message(VoiceError("You are not in a voice channel.")) == \
            "❌ You are not in a voice channel."

    def test_command_not_found_says_nothing_at_all(self):
        # Answering would mean replying to every stray message beginning with
        # the prefix.
        assert errors.user_facing_message(commands.CommandNotFound()) == ""

    def test_an_unexpected_error_is_not_a_user_fault(self):
        assert errors.user_facing_message(RuntimeError("kaboom")) is None


class TestUnwrapping:
    def test_the_wrapper_is_unwrapped_before_matching(self):
        """The tree wraps a handler's exception; reading the wrapper would
        report every failure as "command invoke error"."""
        inner = app_commands.MissingPermissions(["manage_guild"])
        wrapped = app_commands.CommandInvokeError(MagicMock(), inner)
        assert "manage guild" in errors.user_facing_message(wrapped)

    def test_a_wrapped_bug_is_still_a_bug(self):
        wrapped = app_commands.CommandInvokeError(MagicMock(), RuntimeError("kaboom"))
        assert errors.user_facing_message(wrapped) is None


class TestReporting:
    @pytest.mark.asyncio
    async def test_a_user_fault_replies_and_logs_nothing(self, caplog):
        interaction = _interaction()
        with caplog.at_level("ERROR", logger="zephyr.core.errors"):
            await errors.report(interaction, app_commands.CheckFailure("x"))

        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        assert caplog.text == ""

    @pytest.mark.asyncio
    async def test_a_bug_logs_the_traceback_under_a_reference_the_user_is_given(self, caplog):
        interaction = _interaction()
        error = RuntimeError("kaboom")
        with caplog.at_level("ERROR", logger="zephyr.core.errors"):
            await errors.report(interaction, error)

        sent = interaction.response.send_message.await_args.args[0]
        # The whole point of the id: "it said ZP-3F9A2C" has to be enough to
        # find the exact stack.
        reference = sent.split("`")[1]
        assert reference.startswith("ZP-")
        record = caplog.records[0]
        assert record.reference == reference
        assert record.exc_info[1] is error
        assert "RuntimeError: kaboom" in caplog.text

    @pytest.mark.asyncio
    async def test_it_uses_followup_when_the_interaction_was_deferred(self):
        """The load-bearing check. Most of these commands defer first, and
        send_message on a deferred interaction raises -- which would make the
        error handler itself the failure, and put "the application did not
        respond" back on screen."""
        interaction = _interaction(done=True)
        await errors.report(interaction, RuntimeError("kaboom"))

        interaction.followup.send.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_undeliverable_reply_does_not_raise(self, caplog):
        """The interaction may have expired, and an exception raised out of an
        error handler has nowhere left to go."""
        interaction = _interaction()
        interaction.response.send_message = AsyncMock(
            side_effect=discord.HTTPException(MagicMock(status=404), "expired")
        )
        with caplog.at_level("WARNING", logger="zephyr.core.errors"):
            await errors.report(interaction, RuntimeError("kaboom"))
        assert "Could not deliver an error message" in caplog.text

    @pytest.mark.asyncio
    async def test_command_not_found_produces_no_reply(self):
        interaction = _interaction()
        await errors.report(interaction, commands.CommandNotFound())
        interaction.response.send_message.assert_not_awaited()
        interaction.followup.send.assert_not_awaited()

    def test_every_reference_is_distinct(self):
        assert len({errors.new_reference() for _ in range(50)}) == 50


class TestItIsActuallyAttached:
    def test_setup_hook_assigns_the_tree_handler(self):
        """The handler existing is not the same as it being wired.

        ZephyrBot is built with __new__ because __init__ opens a Discord client;
        that is the convention in tests/test_bridge_commands.py.
        """
        import inspect

        from zephyr.client import ZephyrBot

        source = inspect.getsource(ZephyrBot.setup_hook)
        assert "self.tree.on_error" in source
        assert hasattr(ZephyrBot, "on_command_error")

    def test_the_superseded_prefix_hook_is_gone(self):
        from zephyr.cogs.music import MusicCog

        # __dict__, not hasattr: commands.Cog defines cog_command_error on the
        # base class, so hasattr is true for every cog ever written. What
        # matters is that MusicCog no longer *overrides* it -- its version sent
        # a red embed with str(error) for any failure, with no logging and no
        # distinction between a cooldown and a crash.
        assert "cog_command_error" not in MusicCog.__dict__


class TestBotConstruction:
    """13.4. No existing test constructs ZephyrBot.__init__, so these changes
    would otherwise be invisible to the suite by construction."""

    @pytest.fixture
    def bot(self, monkeypatch):
        # __init__ builds no network client, but setup_hook does -- so
        # constructing is safe while starting is not.
        from zephyr.client import ZephyrBot

        return ZephyrBot()

    def test_the_prefix_no_longer_collides_with_slash_commands(self, bot):
        """It was "/", so every message beginning with a slash was also parsed
        as a prefix command: a mistyped "/pley" raised CommandNotFound on a code
        path with no handler."""
        assert bot.command_prefix != "/"
        assert bot.command_prefix == "z!"

    def test_a_mention_is_not_a_prefix(self, bot):
        """A mention is already the AI's trigger in on_message. Accepting it
        here too would make "@Zephyr weather" both ask the AI and run the
        weather command."""
        assert not callable(bot.command_prefix)

    def test_there_is_one_help_implementation(self, bot):
        # DefaultHelpCommand was registered alongside zephyr/cogs/help.py.
        assert bot.help_command is None

    def test_the_intents_are_enumerated_not_all(self, bot):
        """Intents.all() requests every privileged intent, including presences
        and typing, which this bot never reads -- and each has to be justified
        to Discord for verification past 100 guilds."""
        assert bot.intents != discord.Intents.all()
        assert bot.intents.presences is False
        assert bot.intents.typing is False

    def test_but_it_keeps_the_ones_it_actually_uses(self, bot):
        for name in ("guilds", "members", "message_content", "voice_states"):
            assert getattr(bot.intents, name) is True, name
        # on_message has to receive messages at all, in guilds and in DMs.
        assert bot.intents.guild_messages is True
        assert bot.intents.dm_messages is True
