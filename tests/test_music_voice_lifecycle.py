"""Leaving an emptied voice channel, and not leaving dead state behind.

There was no `on_voice_state_update` listener anywhere in this package. The 180s
idle timeout is armed by `async_timeout` around the *queue read* inside
audio_player_task, so it only ever starts once the queue runs dry -- a long
track playing to an empty channel never armed it, and the bot kept streaming to
nobody until the track ended.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.cogs.music import MusicCog, VoiceState


def _cog():
    """MusicCog.__init__ builds a Spotify client, so construct by hand.

    Same convention as tests/test_music_bridge.py.
    """
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.bot.user = MagicMock()
    cog.bot.user.id = 111
    cog.voice_states = {}
    cog._voice_connect_locks = {}
    cog._dj_role_ids = {}
    cog._empty_timers = {}
    cog._clear_snapshot = AsyncMock()
    cog.publish_state = AsyncMock()
    return cog


def _member(user_id, guild_id=7, bot=False):
    member = MagicMock()
    member.id = user_id
    member.bot = bot
    member.guild = MagicMock()
    member.guild.id = guild_id
    return member


def _channel(*members):
    channel = MagicMock()
    channel.members = list(members)
    channel.__str__ = lambda self: "general"  # type: ignore[assignment]
    return channel


def _live_state(cog, channel, guild_id=7):
    state = VoiceState(cog.bot, guild_id)
    state.voice = MagicMock()
    state.voice.channel = channel
    state.voice.is_playing.return_value = True
    state.voice.is_paused.return_value = False
    state.voice.disconnect = AsyncMock()
    state._notify = AsyncMock()
    # Left as None: changed() is deliberately fire-and-forget (it creates a task
    # so the audio player never blocks on Redis), so an AsyncMock here produces
    # a coroutine the test loop never runs -- warning noise, not coverage.
    cog.voice_states[guild_id] = state
    return state


class TestStopClearsTheState:
    @pytest.mark.asyncio
    async def test_stop_marks_the_state_as_gone(self):
        """`exists` is what peek_voice_state reads to answer "is anything
        playing". Only the idle-timeout path used to set it, so every other
        route through stop() left a torn-down state advertising itself as live
        -- and ensure_voice_state then handed that dead object to the next
        /play."""
        cog = _cog()
        state = _live_state(cog, _channel(_member(1)))
        assert state.exists is True

        await state.stop()
        assert state.exists is False
        assert cog.peek_voice_state(7) is None

    @pytest.mark.asyncio
    async def test_teardown_forgets_the_guild_and_clears_the_snapshot(self):
        cog = _cog()
        _live_state(cog, _channel(_member(1)))

        await cog.teardown_voice_state(7)
        # The dict used to accumulate dead states for the life of the process,
        # because only three of the many stop() callers popped it by hand.
        assert 7 not in cog.voice_states
        cog._clear_snapshot.assert_awaited_once_with(7)

    @pytest.mark.asyncio
    async def test_tearing_down_an_unknown_guild_is_a_no_op(self):
        cog = _cog()
        await cog.teardown_voice_state(999)
        cog._clear_snapshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_failing_stop_still_forgets_the_state(self, caplog):
        """Otherwise a broken voice client would wedge the guild permanently."""
        cog = _cog()
        state = _live_state(cog, _channel(_member(1)))
        state.stop = AsyncMock(side_effect=RuntimeError("voice client is gone"))

        with caplog.at_level("ERROR", logger="zephyr.cogs.music"):
            await cog.teardown_voice_state(7)
        assert 7 not in cog.voice_states
        assert "during teardown" in caplog.text


class TestTheBotBeingRemoved:
    @pytest.mark.asyncio
    async def test_a_moderator_disconnecting_the_bot_tears_the_state_down(self):
        """Without this the state stayed in the dict with `exists` True, so the
        dashboard kept showing a player and the next /play reused a state whose
        voice client was gone."""
        cog = _cog()
        _live_state(cog, _channel())
        before, after = MagicMock(), MagicMock()
        after.channel = None

        await cog.on_voice_state_update(_member(111, bot=True), before, after)
        assert 7 not in cog.voice_states

    @pytest.mark.asyncio
    async def test_the_bot_being_moved_is_not_a_disconnect(self):
        cog = _cog()
        _live_state(cog, _channel())
        before, after = MagicMock(), MagicMock()
        after.channel = MagicMock()

        await cog.on_voice_state_update(_member(111, bot=True), before, after)
        assert 7 in cog.voice_states


class TestTheChannelEmptying:
    @pytest.mark.asyncio
    async def test_the_last_listener_leaving_pauses_and_arms_a_timer(self, monkeypatch):
        monkeypatch.setattr("zephyr.cogs.music.EMPTY_CHANNEL_GRACE_SECONDS", 0.01)
        cog = _cog()
        # Only the bot remains.
        channel = _channel(_member(111, bot=True))
        state = _live_state(cog, channel)

        before = MagicMock(); before.channel = channel
        after = MagicMock(); after.channel = None
        await cog.on_voice_state_update(_member(1), before, after)

        # Paused at once -- there is no point decoding audio for an empty room.
        state.voice.pause.assert_called_once()
        assert 7 in cog._empty_timers

        await asyncio.sleep(0.05)
        assert 7 not in cog.voice_states
        # Announced, unlike the idle timeout, which says nothing at all.
        state._notify.assert_awaited()

    @pytest.mark.asyncio
    async def test_somebody_coming_back_cancels_the_timer(self, monkeypatch):
        """The common case is hopping between channels for a few seconds, which
        is why it leaves on a grace timer rather than instantly."""
        monkeypatch.setattr("zephyr.cogs.music.EMPTY_CHANNEL_GRACE_SECONDS", 0.05)
        cog = _cog()
        channel = _channel(_member(111, bot=True))
        _live_state(cog, channel)

        before = MagicMock(); before.channel = channel
        after = MagicMock(); after.channel = None
        await cog.on_voice_state_update(_member(1), before, after)
        assert 7 in cog._empty_timers

        # They rejoin.
        channel.members = [_member(111, bot=True), _member(1)]
        rejoin_before = MagicMock(); rejoin_before.channel = None
        rejoin_after = MagicMock(); rejoin_after.channel = channel
        await cog.on_voice_state_update(_member(1), rejoin_before, rejoin_after)

        assert 7 not in cog._empty_timers
        await asyncio.sleep(0.1)
        assert 7 in cog.voice_states

    @pytest.mark.asyncio
    async def test_it_does_not_leave_if_somebody_returned_during_the_grace(self, monkeypatch):
        """The timer re-checks rather than trusting the state it was armed with."""
        monkeypatch.setattr("zephyr.cogs.music.EMPTY_CHANNEL_GRACE_SECONDS", 0.02)
        cog = _cog()
        channel = _channel(_member(111, bot=True))
        _live_state(cog, channel)

        cog._start_empty_timer(7, channel)
        channel.members = [_member(111, bot=True), _member(2)]
        await asyncio.sleep(0.06)
        assert 7 in cog.voice_states

    @pytest.mark.asyncio
    async def test_a_bot_leaving_does_not_count_as_the_room_emptying(self, monkeypatch):
        """A second music bot leaving must not evict Zephyr."""
        monkeypatch.setattr("zephyr.cogs.music.EMPTY_CHANNEL_GRACE_SECONDS", 0.01)
        cog = _cog()
        channel = _channel(_member(111, bot=True), _member(5))
        _live_state(cog, channel)

        before = MagicMock(); before.channel = channel
        after = MagicMock(); after.channel = None
        await cog.on_voice_state_update(_member(222, bot=True), before, after)
        assert 7 not in cog._empty_timers

    @pytest.mark.asyncio
    async def test_movement_in_an_unrelated_channel_is_ignored(self, monkeypatch):
        monkeypatch.setattr("zephyr.cogs.music.EMPTY_CHANNEL_GRACE_SECONDS", 0.01)
        cog = _cog()
        channel = _channel(_member(111, bot=True))
        _live_state(cog, channel)

        elsewhere = _channel()
        before = MagicMock(); before.channel = elsewhere
        after = MagicMock(); after.channel = None
        await cog.on_voice_state_update(_member(1), before, after)
        assert 7 not in cog._empty_timers

    @pytest.mark.asyncio
    async def test_nothing_happens_when_the_bot_is_not_in_voice_here(self):
        cog = _cog()
        await cog.on_voice_state_update(_member(1), MagicMock(), MagicMock())
        assert cog._empty_timers == {}
