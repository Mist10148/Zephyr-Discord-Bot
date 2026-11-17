"""MusicCog._connect, the one place the player joins voice."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.cogs.music import MusicCog, VoiceError, VoiceState


def _cog(settings=None):
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.voice_states = {}
    cog._voice_connect_locks = {}
    cog._music_settings = settings or {}
    return cog


def _client(channel, connected=True):
    client = MagicMock()
    client.channel = channel
    client.is_connected.return_value = connected
    client.move_to = AsyncMock()
    client.disconnect = AsyncMock()
    return client


def _destination(new_client=None):
    destination = MagicMock()
    destination.connect = AsyncMock(return_value=new_client or _client(destination))
    return destination


@pytest.mark.asyncio
async def test_an_existing_voice_client_is_adopted_not_reconnected():
    """connect() on a guild with a voice client raised "Already connected"."""
    destination = _destination()
    existing = _client(destination)
    guild = SimpleNamespace(id=1, voice_client=existing)
    state = VoiceState(MagicMock(), 1)

    await _cog()._connect(state, guild, destination)

    assert state.voice is existing
    destination.connect.assert_not_awaited()
    existing.move_to.assert_not_awaited()


@pytest.mark.asyncio
async def test_an_existing_client_elsewhere_is_moved():
    destination = _destination()
    existing = _client(MagicMock())
    guild = SimpleNamespace(id=1, voice_client=existing)
    state = VoiceState(MagicMock(), 1)

    await _cog()._connect(state, guild, destination)

    existing.move_to.assert_awaited_once_with(destination)
    assert state.voice is existing


@pytest.mark.asyncio
async def test_a_half_dead_client_is_torn_down_first():
    fresh = _client(None)
    destination = _destination(fresh)
    stale = _client(destination, connected=False)
    guild = SimpleNamespace(id=1, voice_client=stale)
    state = VoiceState(MagicMock(), 1)

    await _cog()._connect(state, guild, destination)

    stale.disconnect.assert_awaited_once()
    assert state.voice is fresh


@pytest.mark.asyncio
async def test_a_failed_connect_is_a_voice_error():
    destination = MagicMock(__str__=lambda self: "General")
    destination.connect = AsyncMock(side_effect=TimeoutError("timed out"))
    guild = SimpleNamespace(id=1, voice_client=None)

    with pytest.raises(VoiceError, match="Could not join General"):
        await _cog()._connect(VoiceState(MagicMock(), 1), guild, destination)


@pytest.mark.asyncio
async def test_an_already_connected_state_does_nothing():
    destination = _destination()
    guild = SimpleNamespace(id=1, voice_client=None)
    state = VoiceState(MagicMock(), 1)
    state.voice = _client(destination)

    await _cog()._connect(state, guild, destination)

    destination.connect.assert_not_awaited()


def test_web_started_playback_posts_to_the_first_music_channel():
    channel = SimpleNamespace(id=77)
    guild = MagicMock(id=1)
    guild.get_channel.side_effect = lambda channel_id: channel if channel_id == 77 else None
    state = VoiceState(MagicMock(), 1)

    _cog({1: {"music_channel_ids": ["5", "77"]}})._ensure_np_channel(state, guild)

    assert state.np_channel_id == 77


def test_a_sticky_channel_is_not_overridden():
    state = VoiceState(MagicMock(), 1, channel_id=12)
    _cog({1: {"music_channel_ids": ["77"]}})._ensure_np_channel(state, MagicMock(id=1))
    assert state.np_channel_id == 12
