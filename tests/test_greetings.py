"""Greetings: the renderer, the storage, and the listener's containment.

The renderer gets the most attention because it is the only place in this
feature where getting it wrong is a security problem rather than a cosmetic one.
A greeting is free text written by a server administrator and rendered against a
live `discord.Member`; `str.format` walks attributes, so
`"{user.guild.me._state.http.token}"` is a working token exfiltration through a
template. The specs below pin that the substitution is literal.
"""

import asyncio
from types import SimpleNamespace

import discord
import pytest

from zephyr.cogs import greetings as cog_module
from zephyr.cogs.greetings import (
    DEFAULT_FAREWELL,
    DEFAULT_WELCOME,
    GreetingsCog,
    render,
)
from zephyr.db import greetings as repo


class _Member:
    def __init__(self, *, name="someone", display="Someone", guild=None):
        self.id = 7
        self.name = name
        self.display_name = display
        self.mention = "<@7>"
        self.guild = guild

    def __str__(self):
        return self.name


def _guild(*, name="A Server", count=42, channel=None):
    guild = SimpleNamespace(id=1, name=name, member_count=count)
    guild.get_channel = lambda _id: channel
    return guild


# ---------------------------------------------------------------------------
# The renderer
# ---------------------------------------------------------------------------


class TestRender:
    def test_it_substitutes_every_placeholder(self):
        text = render("{user}/{mention}/{username}/{server}/{count}", _Member(), _guild())

        assert text == "Someone/<@7>/someone/A Server/42"

    def test_the_defaults_render(self):
        member, guild = _Member(), _guild()

        assert "A Server" in render(DEFAULT_WELCOME, member, guild)
        assert "<@7>" in render(DEFAULT_WELCOME, member, guild)
        assert "Someone" in render(DEFAULT_FAREWELL, member, guild)

    def test_attribute_traversal_is_not_possible(self):
        """The whole reason this is replacement rather than str.format.

        `"{user.guild._state.http.token}".format(user=member)` reads the bot's
        token. Literal replacement leaves an unknown placeholder alone, so the
        template is inert.
        """
        member = _Member()
        member.guild = SimpleNamespace(_state=SimpleNamespace(http=SimpleNamespace(token="SECRET")))

        text = render("{user.guild._state.http.token}", member, _guild())

        assert "SECRET" not in text
        assert text == "{user.guild._state.http.token}"

    def test_a_stray_brace_does_not_raise(self):
        """Which str.format would, on text somebody typed into a Discord modal
        -- and a KeyError in a listener is an invisible failure."""
        assert render("welcome {", _Member(), _guild()) == "welcome {"
        assert render("100% of {nonsense}", _Member(), _guild()) == "100% of {nonsense}"

    def test_it_is_capped_at_discords_message_limit(self):
        """The substitutions grow the text, so a template inside the stored
        limit can still render past 2000 characters."""
        assert len(render("{server}" * 500, _Member(), _guild(name="x" * 100))) == 2000

    def test_a_missing_member_count_renders_as_a_question_mark(self):
        """Not "None": member_count is None until the guild is chunked, and a
        greeting reading "you are member #None" is worse than an honest
        placeholder."""
        guild = _guild()
        guild.member_count = None

        assert "#?" in render("#{count}", _Member(), guild)

    def test_a_member_with_no_nickname_falls_back_to_the_username(self):
        """A discord.User (as opposed to a Member) has no display_name, and a
        repr must never reach a channel."""
        member = SimpleNamespace(name="plain", mention="<@7>")

        assert render("{user}", member, _guild()) == "plain"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


class TestReadAndWrite:
    def test_an_unconfigured_guild_reads_as_none(self, db_url):
        assert repo.read("1", database_url=db_url) is None

    def test_a_write_round_trips(self, db_url):
        repo.write(
            "1",
            {"welcome_enabled": True, "welcome_channel_id": "5", "welcome_message": "hi {user}"},
            database_url=db_url,
        )

        stored = repo.read("1", database_url=db_url)

        assert stored["welcome_enabled"] is True
        assert stored["welcome_message"] == "hi {user}"

    def test_a_partial_write_does_not_blank_the_rest(self, db_url):
        """The same contract write_guild_settings offers: setting the farewell
        channel must not erase the welcome message by omission."""
        repo.write("1", {"welcome_message": "hi", "welcome_enabled": True}, database_url=db_url)
        repo.write("1", {"farewell_channel_id": "9"}, database_url=db_url)

        stored = repo.read("1", database_url=db_url)

        assert stored["welcome_message"] == "hi"
        assert stored["farewell_channel_id"] == "9"

    def test_an_unknown_key_is_ignored_not_written(self, db_url):
        repo.write("1", {"welcome_enabled": True, "guild_id": "999"}, database_url=db_url)

        assert repo.read("999", database_url=db_url) is None

    def test_a_null_flag_reads_as_false_not_none(self, db_url):
        """0008 follows 0005 and leaves the flags nullable, so every consumer
        would otherwise handle three states for a two-state setting."""
        repo.write("1", {"welcome_channel_id": "5"}, database_url=db_url)

        assert repo.read("1", database_url=db_url)["welcome_enabled"] is False


class TestReadAll:
    def test_only_guilds_that_want_a_greeting_are_returned(self, db_url):
        """The listener consults this on every join and leave, so the cache
        should hold the guilds that asked rather than every guild that ever
        opened the settings page."""
        repo.write("1", {"welcome_enabled": True, "welcome_channel_id": "5"}, database_url=db_url)
        repo.write("2", {"welcome_message": "drafted but never enabled"}, database_url=db_url)

        assert set(repo.read_all(database_url=db_url)) == {"1"}

    def test_a_farewell_alone_is_enough(self, db_url):
        repo.write("3", {"farewell_enabled": True}, database_url=db_url)

        assert set(repo.read_all(database_url=db_url)) == {"3"}

    def test_a_disabled_guild_drops_out(self, db_url):
        repo.write("1", {"welcome_enabled": True}, database_url=db_url)
        repo.write("1", {"welcome_enabled": False}, database_url=db_url)

        assert repo.read_all(database_url=db_url) == {}


class TestDeleteForGuild:
    def test_it_forgets_one_guild(self, db_url):
        repo.write("1", {"welcome_enabled": True}, database_url=db_url)
        repo.write("2", {"welcome_enabled": True}, database_url=db_url)

        assert repo.delete_for_guild("1", database_url=db_url) is True
        assert repo.read("1", database_url=db_url) is None
        assert repo.read("2", database_url=db_url) is not None

    def test_deleting_nothing_is_not_an_error(self, db_url):
        assert repo.delete_for_guild("404", database_url=db_url) is False


# ---------------------------------------------------------------------------
# The listener
# ---------------------------------------------------------------------------


class FakeChannel:
    def __init__(self, *, raises=None):
        self.sent = []
        self._raises = raises

    async def send(self, content=None, *, allowed_mentions=None, embed=None):
        if self._raises:
            raise self._raises
        self.sent.append((content, allowed_mentions))


def _cog(cache):
    cog = GreetingsCog.__new__(GreetingsCog)
    cog.bot = SimpleNamespace(intents=SimpleNamespace(members=True))
    cog._cache = cache
    return cog


def _greet(cog, member, kind="welcome"):
    return asyncio.run(cog._greet(member, kind=kind))


class TestGreet:
    def test_an_enabled_welcome_is_posted(self):
        channel = FakeChannel()
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": "5",
                          "welcome_message": "hi {user}"}})
        member = _Member(guild=_guild(channel=channel))

        assert _greet(cog, member) is True
        assert channel.sent[0][0] == "hi Someone"

    def test_no_configuration_posts_nothing(self):
        channel = FakeChannel()
        assert _greet(_cog({}), _Member(guild=_guild(channel=channel))) is False
        assert channel.sent == []

    def test_a_disabled_greeting_posts_nothing(self):
        channel = FakeChannel()
        cog = _cog({"1": {"welcome_enabled": False, "welcome_channel_id": "5"}})

        assert _greet(cog, _Member(guild=_guild(channel=channel))) is False

    def test_a_welcome_does_not_fire_on_a_leave(self):
        """One cache entry serves both, so a kind that ignored its own flag
        would post a welcome when somebody left."""
        channel = FakeChannel()
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": "5",
                          "farewell_enabled": False}})

        assert _greet(cog, _Member(guild=_guild(channel=channel)), kind="farewell") is False

    def test_the_default_is_used_when_no_message_is_stored(self):
        """NULL means "use the default", which is why the command stores None
        rather than "" -- Discord rejects an empty message."""
        channel = FakeChannel()
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": "5",
                          "welcome_message": None}})

        _greet(cog, _Member(guild=_guild(channel=channel)))

        assert "A Server" in channel.sent[0][0]

    def test_everyone_is_never_pinged(self):
        """One saved template containing @everyone would otherwise ping the
        whole server on every single join."""
        channel = FakeChannel()
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": "5",
                          "welcome_message": "@everyone {mention} is here"}})

        _greet(cog, _Member(guild=_guild(channel=channel)))
        allowed = channel.sent[0][1]

        assert allowed.everyone is False
        assert allowed.roles is False
        # The person being welcomed is still mentioned -- that is the point.
        assert allowed.users is True

    def test_a_missing_channel_is_logged_not_raised(self, caplog):
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": "5"}})

        with caplog.at_level("WARNING", logger="zephyr.cogs.greetings"):
            assert _greet(cog, _Member(guild=_guild(channel=None))) is False
        assert "is gone" in caplog.text

    def test_enabled_with_no_channel_is_not_an_error(self, caplog):
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": None}})

        with caplog.at_level("INFO", logger="zephyr.cogs.greetings"):
            assert _greet(cog, _Member(guild=_guild())) is False

    def test_a_forbidden_channel_does_not_raise(self, caplog):
        channel = FakeChannel(raises=discord.Forbidden(_Response(403), "no"))
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": "5"}})

        with caplog.at_level("WARNING", logger="zephyr.cogs.greetings"):
            assert _greet(cog, _Member(guild=_guild(channel=channel))) is False

    def test_an_unexpected_failure_does_not_raise(self, caplog):
        """An unhandled exception in a listener is logged by discord.py and
        otherwise invisible, and every subsequent join would raise again."""
        channel = FakeChannel(raises=RuntimeError("boom"))
        cog = _cog({"1": {"welcome_enabled": True, "welcome_channel_id": "5"}})

        with caplog.at_level("ERROR", logger="zephyr.cogs.greetings"):
            assert _greet(cog, _Member(guild=_guild(channel=channel))) is False

    def test_a_member_with_no_guild_is_ignored(self):
        assert _greet(_cog({"1": {"welcome_enabled": True}}), _Member(guild=None)) is False


class TestLeavingAGuild:
    def test_greetings_are_forgotten(self, db_url, monkeypatch):
        """A stored channel id points into a server this deployment can no
        longer see, and read_all would carry it forever."""
        repo.write("1", {"welcome_enabled": True, "welcome_channel_id": "5"}, database_url=db_url)
        # Captured first: cog_module.repo *is* repo, so patching the name in
        # place would make the stub call itself.
        real_delete = repo.delete_for_guild
        real_read = repo.read
        monkeypatch.setattr(
            cog_module.repo, "delete_for_guild",
            lambda guild_id: real_delete(guild_id, database_url=db_url),
        )
        cog = _cog({"1": {"welcome_enabled": True}})

        asyncio.run(cog.on_guild_remove(SimpleNamespace(id=1)))

        assert real_read("1", database_url=db_url) is None
        assert cog._cache == {}

    def test_a_failed_delete_still_drops_the_cache_entry(self, monkeypatch, caplog):
        """The bot cannot post there either way, so the in-memory half must not
        depend on the database half succeeding."""
        def boom(_guild_id):
            raise RuntimeError("no db")

        monkeypatch.setattr(cog_module.repo, "delete_for_guild", boom)
        cog = _cog({"1": {"welcome_enabled": True}})

        with caplog.at_level("ERROR", logger="zephyr.cogs.greetings"):
            asyncio.run(cog.on_guild_remove(SimpleNamespace(id=1)))

        assert cog._cache == {}


class _Response:
    def __init__(self, status):
        self.status = status
        self.reason = "Forbidden"


class TestTheIntentWarning:
    def test_a_missing_members_intent_is_reported_at_startup(self, caplog):
        """The failure mode is a greeting that never fires with nothing in any
        log, which is the kind of thing that gets debugged in six months."""
        cog = GreetingsCog.__new__(GreetingsCog)
        cog.bot = SimpleNamespace(intents=SimpleNamespace(members=False))
        cog._cache = {}
        cog._refresh_loop = SimpleNamespace(start=lambda: None)

        with caplog.at_level("ERROR", logger="zephyr.cogs.greetings"):
            asyncio.run(cog.cog_load())

        assert "Server Members intent" in caplog.text

    def test_nothing_is_said_when_the_intent_is_on(self, caplog):
        cog = GreetingsCog.__new__(GreetingsCog)
        cog.bot = SimpleNamespace(intents=SimpleNamespace(members=True))
        cog._cache = {}
        cog._refresh_loop = SimpleNamespace(start=lambda: None)

        with caplog.at_level("ERROR", logger="zephyr.cogs.greetings"):
            asyncio.run(cog.cog_load())

        assert "Server Members intent" not in caplog.text

    def test_the_bot_actually_requests_the_intent(self):
        """The warning above would be a permanent false alarm otherwise."""
        import inspect

        from zephyr import client

        assert "intents.members = True" in inspect.getsource(client.ZephyrBot.__init__)


class TestTheCacheSurvivesAFailedRefresh:
    def test_a_failed_read_keeps_the_old_cache(self, monkeypatch, caplog):
        """Serving a slightly stale greeting beats serving none because one
        read failed."""
        def boom():
            raise RuntimeError("no db")

        monkeypatch.setattr(cog_module.repo, "read_all", boom)
        cog = _cog({"1": {"welcome_enabled": True}})

        with caplog.at_level("ERROR", logger="zephyr.cogs.greetings"):
            asyncio.run(GreetingsCog._refresh_loop.coro(cog))

        assert cog._cache == {"1": {"welcome_enabled": True}}


class TestTheCogIsRegistered:
    def test_greetings_is_enabled(self):
        from zephyr import config

        assert "greetings" in config.ENABLED_COGS
