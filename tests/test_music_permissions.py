"""The player permission rule, applied to slash commands too."""

import inspect
from unittest.mock import AsyncMock, MagicMock

import pytest
from discord import app_commands

from zephyr.cogs.music import MusicCog, VoiceState
from zephyr.cogs.voice_tts import TTSCog


def _cog(dj_role_id=None, state=None):
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.voice_states = {1: state} if state else {}
    cog._dj_role_ids = {1: dj_role_id} if dj_role_id else {}
    return cog


def _interaction(*, roles=(), manage_guild=False, voice_channel=None):
    member = MagicMock(id=42)
    member.guild_permissions.manage_guild = manage_guild
    member.roles = [MagicMock(id=role) for role in roles]
    member.voice = MagicMock(channel=voice_channel) if voice_channel else None
    guild = MagicMock(id=1)
    guild.get_member.return_value = member
    interaction = MagicMock(guild=guild, user=member)
    interaction.response.send_message = AsyncMock()
    return interaction


def _playing_state(channel):
    state = VoiceState(MagicMock(), 1)
    state.voice = MagicMock()
    state.voice.is_connected.return_value = True
    state.voice.channel = channel
    return state


@pytest.mark.asyncio
async def test_a_member_without_the_dj_role_is_refused():
    interaction = _interaction(roles=[5])
    assert not await _cog(dj_role_id="9")._gate(interaction)
    assert "DJ role" in interaction.response.send_message.await_args.args[0]


@pytest.mark.asyncio
async def test_a_dj_is_allowed():
    assert await _cog(dj_role_id="9")._gate(_interaction(roles=[9]))


@pytest.mark.asyncio
async def test_without_a_dj_role_you_must_be_listening():
    channel, elsewhere = MagicMock(), MagicMock()
    cog = _cog(state=_playing_state(channel))
    assert await cog._gate(_interaction(voice_channel=channel))
    assert not await cog._gate(_interaction(voice_channel=elsewhere))


@pytest.mark.asyncio
async def test_server_managers_always_pass():
    assert await _cog(dj_role_id="9")._gate(_interaction(manage_guild=True))


@pytest.mark.parametrize("name", [
    "pause", "resume", "stop", "clear", "seek", "forward", "rewind", "shuffle", "remove", "move",
    "jump", "loop", "loopqueue", "volume", "twenty_four_seven", "reset_effects", "autoplay", "pitch",
    "_toggle_effect", "_set_bass_boost",
])
def test_every_player_command_is_gated(name):
    member = getattr(MusicCog, name)
    function = member.callback if isinstance(member, app_commands.Command) else member
    assert "self._gate(interaction)" in inspect.getsource(function)


def test_pause_resume_and_stop_no_longer_demand_manage_server():
    """A DJ could press the pause button but not run /pause."""
    for name in ("pause", "resume", "stop"):
        assert not getattr(MusicCog, name).checks, name


def test_disconnect_needs_manage_server_like_leave():
    assert TTSCog.disconnect.checks
