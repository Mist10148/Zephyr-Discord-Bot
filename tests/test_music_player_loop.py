"""The audio player loop's recovery paths.

Each test here is a way the loop used to stall or tear itself down: a slow
lookup mistaken for the idle timeout, a failed seek that nobody woke the loop
after, and a stale after-callback skipping the restarted track.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.cogs.music import Track, VoiceState, YTDLSource


class Voice:
    def __init__(self):
        self.played = []
        self.after = None

    def play(self, source, *, after):
        self.played.append(source)
        self.after = after

    def stop(self):
        pass

    def is_connected(self):
        return True

    def is_playing(self):
        return bool(self.played)

    def is_paused(self):
        return False


async def _spin(predicate, attempts=50):
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0)


def _state():
    bot = MagicMock()
    bot.loop = asyncio.get_running_loop()
    state = VoiceState(bot, guild_id=1)
    state.voice = Voice()
    state._notify = AsyncMock()
    state.announce_now_playing = AsyncMock()
    state.stop = AsyncMock()
    return state


@pytest.mark.asyncio
async def test_a_lookup_timeout_skips_the_track_instead_of_disconnecting(monkeypatch):
    state = _state()
    slow, good = Track(title="Slow"), Track(title="Good")
    state.songs.put_nowait(slow)
    state.songs.put_nowait(good)
    source = MagicMock()
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(side_effect=[asyncio.TimeoutError(), source]))

    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: state.voice.played)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    state.stop.assert_not_awaited()
    assert state.voice.played == [source]
    assert state.current is good


@pytest.mark.asyncio
async def test_a_dead_track_is_not_requeued_forever_in_queue_loop(monkeypatch):
    state = _state()
    state.loop = "queue"
    dead = Track(title="Deleted video")
    state.songs.put_nowait(dead)
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(side_effect=RuntimeError("Video unavailable")))

    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: YTDLSource.from_track.await_count >= 1 and not len(state.songs))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert list(state.songs) == []
    assert YTDLSource.from_track.await_count == 1


@pytest.mark.asyncio
async def test_a_failed_restart_wakes_the_player(monkeypatch):
    state = _state()
    state.current = Track(title="Playing")
    state.source = MagicMock()
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(side_effect=RuntimeError("extract failed")))

    with pytest.raises(RuntimeError):
        await state.restart_current(preserve_position=True)

    assert state.next.is_set()
    assert state.source is None


@pytest.mark.asyncio
async def test_the_replaced_sources_callback_never_advances_the_queue(monkeypatch):
    """However late the old callback lands, it is recognised by identity."""
    state = _state()
    old = MagicMock(stderr_tail="")
    new = MagicMock(stderr_tail="")
    state.current = Track(title="Playing")
    state.source = old
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(return_value=new))

    await state.restart_current(preserve_position=True)
    # Old callback arrives *after* the restart finished (the file-source race).
    state.play_next_song(None, source=old, track=state.current)
    await asyncio.sleep(0)

    assert not state.next.is_set()
    assert state.source is new
    # The new source's own end still advances.
    state.voice.after(None)
    await _spin(lambda: state.next.is_set())
    assert state.next.is_set()


class PausableVoice(Voice):
    def __init__(self, paused=False):
        super().__init__()
        self.paused = paused
        self.playing_now = None

    def play(self, source, *, after):
        if self.playing_now is not None:
            raise RuntimeError("Already playing audio.")
        super().play(source, after=after)
        self.playing_now = source
        self.paused = False

    def stop(self):
        self.playing_now = None

    def pause(self):
        self.paused = True

    def is_paused(self):
        return self.paused


@pytest.mark.asyncio
async def test_overlapping_restarts_do_not_collide(monkeypatch):
    """Two quick effect toggles: the second play() used to raise mid-playback."""
    state = _state()
    state.voice = PausableVoice()
    state.current = Track(title="Playing")
    state.source = MagicMock(stderr_tail="")
    gate = asyncio.Event()

    async def slow_lookup(track, **kwargs):
        await gate.wait()
        return MagicMock(stderr_tail="")

    monkeypatch.setattr(YTDLSource, "from_track", slow_lookup)
    first = asyncio.create_task(state.restart_current(preserve_position=True))
    second = asyncio.create_task(state.restart_current(preserve_position=True))
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(first, second)

    assert len(state.voice.played) == 2
    assert not state.next.is_set()
    # Both replaced sources are ignored whenever their callbacks land.
    assert len(state._retired_sources) == 2


@pytest.mark.asyncio
async def test_restarting_a_paused_track_keeps_it_paused(monkeypatch):
    state = _state()
    state.voice = PausableVoice(paused=True)
    state.current = Track(title="Paused")
    state.source = MagicMock(stderr_tail="")
    state._current_position, state._current_start_time = 42.0, None
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(return_value=MagicMock(stderr_tail="")))

    await state.restart_current(preserve_position=True)

    assert state.voice.paused is True
    assert state._current_start_time is None
    assert state.elapsed == 42.0


@pytest.mark.asyncio
async def test_a_skip_during_the_restart_lookup_still_skips(monkeypatch):
    state = _state()
    state.voice = PausableVoice()
    state.current = Track(title="Playing")
    state.source = MagicMock(stderr_tail="")
    replacement = MagicMock(stderr_tail="")
    gate = asyncio.Event()

    async def slow_lookup(track, **kwargs):
        await gate.wait()
        return replacement

    monkeypatch.setattr(YTDLSource, "from_track", slow_lookup)
    restart = asyncio.create_task(state.restart_current(preserve_position=True))
    await asyncio.sleep(0)
    state.skip()
    gate.set()
    await restart

    assert state.voice.played == []
    replacement.cleanup.assert_called_once()
    assert state.next.is_set()
