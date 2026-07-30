"""Tests for SongQueue and the VoiceState helpers.

Both were completely uncovered, which is how a cross-thread asyncio.Event.set and an
O(n^2) shuffle survived. No network calls and no voice connection: SongQueue is pure
data, and VoiceState only needs a MagicMock bot.
"""

import asyncio
from unittest.mock import MagicMock

import pytest

from zephyr.cogs.music import SongQueue, VoiceState


def _make_state(guild_id=123456789, channel_id=987654321):
    """A VoiceState with a mocked bot; touches no voice client and no event loop.

    VoiceState takes ids rather than a Context precisely so it can be built like
    this -- and so the Redis bridge, which has no Context, can reach it.
    """
    return VoiceState(MagicMock(), guild_id, channel_id=channel_id)


class TestSongQueuePut:
    def test_put_nowait_appends(self):
        queue = SongQueue()
        queue.put_nowait("a")
        queue.put_nowait("b")
        assert list(queue) == ["a", "b"]

    def test_add_to_front_prepends(self):
        queue = SongQueue()
        queue.put_nowait("a")
        queue.add_to_front("b")
        assert list(queue) == ["b", "a"]

    def test_len_and_clear(self):
        queue = SongQueue()
        for item in "abc":
            queue.put_nowait(item)
        assert len(queue) == 3
        queue.clear()
        assert len(queue) == 0


class TestSongQueueGet:
    @pytest.mark.asyncio
    async def test_get_pops_from_the_front(self):
        queue = SongQueue()
        queue.put_nowait("a")
        queue.put_nowait("b")
        assert await queue.get() == "a"
        assert await queue.get() == "b"

    @pytest.mark.asyncio
    async def test_get_blocks_until_an_item_arrives(self):
        """The queue-empty wait is what the idle timeout wraps, so it must block."""
        queue = SongQueue()
        getter = asyncio.create_task(queue.get())
        await asyncio.sleep(0)
        assert not getter.done()
        queue.put_nowait("late")
        assert await asyncio.wait_for(getter, timeout=1) == "late"


class TestSongQueueIndexing:
    def test_integer_index(self):
        queue = SongQueue()
        for item in "abc":
            queue.put_nowait(item)
        assert queue[0] == "a"
        assert queue[2] == "c"

    def test_slice_returns_a_list(self):
        """The /queue command pages with a slice."""
        queue = SongQueue()
        for item in "abcde":
            queue.put_nowait(item)
        assert queue[1:3] == ["b", "c"]
        assert queue[:2] == ["a", "b"]

    def test_out_of_range_raises(self):
        queue = SongQueue()
        queue.put_nowait("a")
        with pytest.raises(IndexError):
            queue[5]


class TestSongQueueRemove:
    def test_remove_by_index(self):
        queue = SongQueue()
        for item in "abc":
            queue.put_nowait(item)
        queue.remove(1)
        assert list(queue) == ["a", "c"]

    def test_remove_out_of_range_raises(self):
        queue = SongQueue()
        with pytest.raises(IndexError):
            queue.remove(0)


class TestSongQueueMove:
    def test_move_forward(self):
        queue = SongQueue()
        for item in "abcd":
            queue.put_nowait(item)
        queue.move(0, 2)
        assert list(queue) == ["b", "c", "a", "d"]

    def test_move_backward(self):
        queue = SongQueue()
        for item in "abcd":
            queue.put_nowait(item)
        queue.move(3, 0)
        assert list(queue) == ["d", "a", "b", "c"]

    def test_move_to_itself_is_a_no_op(self):
        queue = SongQueue()
        for item in "abc":
            queue.put_nowait(item)
        queue.move(1, 1)
        assert list(queue) == ["a", "b", "c"]

    @pytest.mark.parametrize("args", [(-1, 0), (5, 0), (0, -1), (0, 5)])
    def test_out_of_bounds_indices_raise(self, args):
        queue = SongQueue()
        for item in "abc":
            queue.put_nowait(item)
        with pytest.raises(IndexError):
            queue.move(*args)


class TestSongQueueShuffle:
    def test_shuffle_preserves_every_item(self):
        queue = SongQueue()
        for index in range(50):
            queue.put_nowait(index)
        queue.shuffle()
        assert sorted(queue) == list(range(50))
        assert len(queue) == 50

    def test_shuffle_still_supports_deque_operations(self):
        """shuffle rebuilds the container, so it must remain a deque."""
        queue = SongQueue()
        for index in range(5):
            queue.put_nowait(index)
        queue.shuffle()
        queue.add_to_front("front")
        assert queue[0] == "front"
        assert len(queue) == 6

    def test_shuffling_an_empty_queue_is_harmless(self):
        queue = SongQueue()
        queue.shuffle()
        assert len(queue) == 0


class TestPlayNextSong:
    """discord.py calls this from its AudioPlayer thread, not the event loop."""

    def test_marshals_the_event_onto_the_loop(self):
        state = _make_state()
        state.bot.loop = MagicMock()
        state.play_next_song()
        state.bot.loop.call_soon_threadsafe.assert_called_once_with(state.next.set)

    def test_manual_stop_short_circuits_and_resets(self):
        """restart_current uses this flag to suppress one queue advance."""
        state = _make_state()
        state.bot.loop = MagicMock()
        state.manual_stop = True
        state.play_next_song()
        state.bot.loop.call_soon_threadsafe.assert_not_called()
        assert state.manual_stop is False

    def test_a_closed_loop_does_not_raise(self):
        state = _make_state()
        state.bot.loop = MagicMock()
        state.bot.loop.call_soon_threadsafe.side_effect = RuntimeError("loop is closed")
        state.play_next_song()

    def test_an_ffmpeg_error_is_logged_but_still_advances(self, capsys):
        state = _make_state()
        state.bot.loop = MagicMock()
        state.play_next_song(error=Exception("boom"))
        assert "FFmpeg Error" in capsys.readouterr().out
        state.bot.loop.call_soon_threadsafe.assert_called_once()

    @pytest.mark.asyncio
    async def test_the_event_really_wakes_a_waiter_from_another_thread(self):
        """The behavioural counterpart: a direct next.set() from a thread is unsafe."""
        state = _make_state()
        state.bot.loop = asyncio.get_running_loop()
        waiter = asyncio.create_task(state.next.wait())
        await asyncio.sleep(0)
        await asyncio.to_thread(state.play_next_song)
        await asyncio.wait_for(waiter, timeout=1)


class TestVoiceStateDefaults:
    def test_effects_and_247_start_disabled(self):
        """/join used to force _247_enabled on, disabling the idle timeout forever."""
        state = _make_state()
        assert state._247_enabled is False
        assert state._loop_mode == "off"
        assert state._pitch == 1.0
        assert state._bass_boost is None
        for flag in ("_16d_enabled", "_reverb_enabled", "_slowed_enabled",
                     "_slownrev_enabled", "_nightcore_enabled", "_vaporwave_enabled"):
            assert getattr(state, flag) is False, flag

    def test_volume_setter_clamps_nothing_but_tracks_the_value(self):
        state = _make_state()
        state.volume = 0.8
        assert state.volume == 0.8

    def test_elapsed_is_zero_before_playback(self):
        state = _make_state()
        assert state.elapsed == 0

    def test_is_playing_is_false_without_a_voice_client(self):
        state = _make_state()
        assert not state.is_playing

    def test_constructs_from_ids_with_no_context(self):
        """What lets the Redis bridge reach a guild's state at all."""
        state = _make_state(guild_id=42, channel_id=7)
        assert state.guild_id == 42
        assert state.np_channel_id == 7
        assert state.current is None
        assert state.source is None

    def test_the_sticky_channel_may_be_unset(self):
        state = VoiceState(MagicMock(), 42)
        assert state.np_channel_id is None
        assert state.channel() is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
