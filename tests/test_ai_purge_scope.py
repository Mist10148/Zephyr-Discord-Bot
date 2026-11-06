"""The bot's half of a dashboard memory purge must purge what was asked for."""

import asyncio

from zephyr.cogs import chat as chat_module
from zephyr.cogs.chat import ChatCog


class FakeGuild:
    id = 1


def _run(cog, guild, actor_id, args, monkeypatch):
    calls = []
    monkeypatch.setattr(chat_module, "clear_history_for_context",
                        lambda guild_id, user_id: calls.append((guild_id, user_id)) or True)
    result = asyncio.run(cog._bridge_purge_memory(guild, actor_id, args))
    return result, calls


def test_a_guild_purge_the_bot_cannot_see_never_touches_dm_memory(monkeypatch):
    """get_guild() is None when the bot left the guild; that is not a DM."""
    result, calls = _run(ChatCog(bot=None), None, "900", {"scope": "guild", "channel_id": "5"}, monkeypatch)

    assert calls == []
    assert result == {"cleared": False}


def test_a_dm_purge_clears_the_owners_buffer(monkeypatch):
    _, calls = _run(ChatCog(bot=None), None, "900", {"scope": "dm"}, monkeypatch)
    assert calls == [(None, 900)]


def test_a_guild_purge_clears_the_guild_buffer(monkeypatch):
    _, calls = _run(ChatCog(bot=None), FakeGuild(), "900", {"scope": "guild"}, monkeypatch)
    assert calls == [(1, None)]
