"""Where the AI is allowed to answer.

The mention/reply handler answered anywhere the bot could read, so a server that
wanted Zephyr for music had no way to stop people conversing with it in every
channel.

This adds a restriction rather than imposing one: a guild with no policy still
answers everywhere, which every case here asserts one way or another.
"""

from unittest.mock import MagicMock

import discord
import pytest

from zephyr.client import ZephyrBot
from zephyr.db.guild_settings import read_ai_channel_policies, write_guild_settings


def _bot(policies=None):
    bot = ZephyrBot.__new__(ZephyrBot)
    bot._ai_channel_policies = policies or {}
    return bot


def _message(guild_id=7, channel_id=100, parent_id=None):
    message = MagicMock()
    if guild_id is None:
        message.guild = None
        message.channel = MagicMock(spec=discord.DMChannel)
        return message
    message.guild = MagicMock()
    message.guild.id = guild_id
    message.channel = MagicMock()
    message.channel.id = channel_id
    # spec-less MagicMock invents a parent_id, so it has to be set explicitly.
    message.channel.parent_id = parent_id
    return message


class TestNoPolicy:
    def test_a_guild_with_no_policy_answers_everywhere(self):
        assert _bot().ai_may_answer(_message()) is True

    def test_a_dm_always_answers(self):
        assert _bot({"7": ("allow", {"999"})}).ai_may_answer(_message(guild_id=None)) is True

    def test_another_guilds_policy_does_not_apply(self):
        bot = _bot({"8": ("deny", {"100"})})
        assert bot.ai_may_answer(_message(guild_id=7, channel_id=100)) is True


class TestAllowList:
    def test_a_listed_channel_answers(self):
        bot = _bot({"7": ("allow", {"100"})})
        assert bot.ai_may_answer(_message(channel_id=100)) is True

    def test_an_unlisted_channel_does_not(self):
        bot = _bot({"7": ("allow", {"100"})})
        assert bot.ai_may_answer(_message(channel_id=200)) is False


class TestDenyList:
    def test_a_listed_channel_does_not_answer(self):
        bot = _bot({"7": ("deny", {"100"})})
        assert bot.ai_may_answer(_message(channel_id=100)) is False

    def test_everywhere_else_does(self):
        bot = _bot({"7": ("deny", {"100"})})
        assert bot.ai_may_answer(_message(channel_id=200)) is True


class TestThreads:
    def test_a_thread_inherits_its_parents_allow(self):
        """A policy naming #bot-spam should cover threads started in it."""
        bot = _bot({"7": ("allow", {"100"})})
        assert bot.ai_may_answer(_message(channel_id=555, parent_id=100)) is True

    def test_a_thread_inherits_its_parents_deny(self):
        """Otherwise anyone could bypass the policy by opening a thread."""
        bot = _bot({"7": ("deny", {"100"})})
        assert bot.ai_may_answer(_message(channel_id=555, parent_id=100)) is False

    def test_a_thread_under_an_unlisted_parent_follows_the_mode(self):
        assert _bot({"7": ("allow", {"100"})}).ai_may_answer(_message(channel_id=555, parent_id=200)) is False
        assert _bot({"7": ("deny", {"100"})}).ai_may_answer(_message(channel_id=555, parent_id=200)) is True


class TestTheRepo:
    def test_it_omits_guilds_with_no_mode(self, db_url):
        write_guild_settings("7", {"ai_channel_mode": "deny", "ai_channel_ids": ["100"]}, database_url=db_url)
        write_guild_settings("8", {"prefix": "!"}, database_url=db_url)

        policies = read_ai_channel_policies(database_url=db_url)
        assert policies == {"7": ("deny", {"100"})}

    def test_an_empty_allowlist_is_treated_as_no_policy(self, db_url):
        """An allowlist with nothing in it would silence the AI everywhere,
        which is never what somebody meant to configure -- it is what a
        half-finished edit looks like."""
        write_guild_settings("7", {"ai_channel_mode": "allow", "ai_channel_ids": []}, database_url=db_url)
        assert read_ai_channel_policies(database_url=db_url) == {}

    def test_an_empty_denylist_is_kept_and_denies_nothing(self, db_url):
        """Unlike an empty allowlist this is coherent: deny nothing = allow
        everything, so it does not need special-casing."""
        write_guild_settings("7", {"ai_channel_mode": "deny", "ai_channel_ids": []}, database_url=db_url)
        policies = read_ai_channel_policies(database_url=db_url)
        assert policies == {"7": ("deny", set())}
        assert _bot(policies).ai_may_answer(_message()) is True


class TestTheApi:
    def _headers(self, logged_in):
        return {"X-Zephyr-CSRF": logged_in.csrf}

    def test_a_policy_can_be_saved(self, client, logged_in, fake_redis, db_url):
        response = client.patch(
            "/api/v1/guilds/1/settings",
            json={"ai_channel_mode": "deny", "ai_channel_ids": ["100"]},
            headers=self._headers(logged_in),
        )
        assert response.status_code == 200
        assert response.get_json()["ai_channel_mode"] == "deny"

    def test_an_unconfigured_guild_reports_no_policy(self, client, logged_in, fake_redis):
        body = client.get("/api/v1/guilds/1").get_json()
        assert body["ai_channel_mode"] is None
        assert body["ai_channel_ids"] == []

    @pytest.mark.parametrize("value", ["everywhere", "block", "ALLOWLIST"])
    def test_an_unknown_mode_is_refused(self, client, logged_in, fake_redis, value):
        response = client.patch(
            "/api/v1/guilds/1/settings",
            json={"ai_channel_mode": value},
            headers=self._headers(logged_in),
        )
        assert response.status_code == 400

    def test_clearing_the_mode_is_allowed(self, client, logged_in, fake_redis):
        """"all" and null both mean "no policy", so the UI can offer a
        three-way choice without a fourth value meaning the same thing."""
        for value in (None, "", "all"):
            response = client.patch(
                "/api/v1/guilds/1/settings",
                json={"ai_channel_mode": value},
                headers=self._headers(logged_in),
            )
            assert response.status_code == 200, value
            assert response.get_json()["ai_channel_mode"] is None
