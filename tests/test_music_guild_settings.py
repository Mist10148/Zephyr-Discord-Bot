"""The dashboard's default volume and music channels reach the bot."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from zephyr.cogs.music import MusicCog
from zephyr.db.guild_settings import read_music_settings, write_guild_settings


def test_only_guilds_with_music_settings_are_returned(db_url):
    write_guild_settings("1", {"default_volume": 80}, database_url=db_url)
    write_guild_settings("2", {"music_channel_ids": ["77", "78"]}, database_url=db_url)
    write_guild_settings("3", {"prefix": "!"}, database_url=db_url)

    settings = read_music_settings(database_url=db_url)

    assert settings["1"] == {"default_volume": 80, "music_channel_ids": []}
    assert settings["2"] == {"default_volume": None, "music_channel_ids": ["77", "78"]}
    assert "3" not in settings


def _cog(settings):
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.voice_states = {}
    cog._music_settings = settings
    return cog


def test_a_new_player_starts_at_the_default_volume():
    state = _cog({5: {"default_volume": 80, "music_channel_ids": []}}).ensure_voice_state(5)
    assert state.volume == 0.8


def test_without_a_default_the_player_keeps_its_own():
    state = _cog({}).ensure_voice_state(5)
    assert state.volume == 0.5


def _interaction(channel_id, *, manage_guild=False):
    return SimpleNamespace(
        guild=SimpleNamespace(id=5),
        channel_id=channel_id,
        permissions=SimpleNamespace(manage_guild=manage_guild),
        response=SimpleNamespace(send_message=AsyncMock()),
    )


def test_music_commands_are_limited_to_the_configured_channels():
    cog = _cog({5: {"default_volume": None, "music_channel_ids": ["77"]}})

    assert asyncio.run(cog.interaction_check(_interaction(77))) is True
    outside = _interaction(99)
    assert asyncio.run(cog.interaction_check(outside)) is False
    assert "<#77>" in outside.response.send_message.await_args.args[0]


def test_managers_are_never_locked_out():
    cog = _cog({5: {"default_volume": None, "music_channel_ids": ["77"]}})
    assert asyncio.run(cog.interaction_check(_interaction(99, manage_guild=True))) is True


def test_no_channel_list_means_every_channel():
    assert asyncio.run(_cog({}).interaction_check(_interaction(99))) is True
