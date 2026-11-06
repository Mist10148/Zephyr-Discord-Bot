"""Stop/skip intents, /save, the snapshot loop and the voice-kick listener."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.cogs.music import MusicCog, Track, VoiceState, YTDLSource


class Voice:
    def __init__(self):
        self.played = []
        self.after = None
        self.stopped = 0

    def play(self, source, *, after):
        self.played.append(source)
        self.after = after

    def stop(self):
        self.stopped += 1
        if self.after:
            after, self.after = self.after, None
            after(None)

    def is_connected(self):
        return True

    def is_playing(self):
        return self.after is not None

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
    return state


async def _run_until(state, predicate):
    task = asyncio.create_task(state.audio_player_task())
    await _spin(predicate)
    return task


async def _cancel(task):
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_skip_moves_on_even_under_loop_track(monkeypatch):
    state = _state()
    state.loop = "track"
    first, second = Track(title="One"), Track(title="Two")
    state.songs.put_nowait(first)
    state.songs.put_nowait(second)
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(side_effect=lambda track, **_: MagicMock(stderr_tail="", track=track)))

    task = await _run_until(state, lambda: state.current is first and state.voice.played)
    state.skip()
    await _spin(lambda: state.current is second)
    await _cancel(task)

    assert state.current is second


@pytest.mark.asyncio
async def test_stop_is_not_undone_by_loop_track_or_autoplay(monkeypatch):
    state = _state()
    state.loop = "track"
    state._autoplay_enabled = True
    state._extend_with_radio = AsyncMock(return_value=0)
    track = Track(title="One")
    state.songs.put_nowait(track)
    state.songs.put_nowait(Track(title="Two"))
    factory = AsyncMock(side_effect=lambda track, **_: MagicMock(stderr_tail=""))
    monkeypatch.setattr(YTDLSource, "from_track", factory)

    task = await _run_until(state, lambda: state.voice.played)
    assert state.halt() is True
    await _spin(lambda: state.current is None)
    await asyncio.sleep(0)
    await _cancel(task)

    assert factory.await_count == 1
    assert list(state.songs) == []
    state._extend_with_radio.assert_not_awaited()


def test_save_does_not_duplicate_the_current_track_in_queue_loop():
    state = VoiceState(MagicMock(), guild_id=1)
    current, later = Track(title="Now"), Track(title="Later")
    state.current = current
    state.loop = "queue"
    state.songs.put_nowait(later)
    state.songs.put_nowait(current)  # queue-loop re-appended it

    titles = [row["title"] for row in MusicCog._queue_payload(None, state)]

    assert titles == ["Now", "Later"]


def _cog():
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.voice_states = {}
    return cog


@pytest.mark.asyncio
async def test_an_idle_state_is_not_published_every_tick(monkeypatch):
    cog = _cog()
    cog.voice_states[1] = VoiceState(MagicMock(), guild_id=1)  # created by /queue, never played
    cog.publish_state = AsyncMock()

    await MusicCog._snapshot_loop.coro(cog)

    cog.publish_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_being_kicked_from_voice_tears_the_player_down():
    cog = _cog()
    cog.bot.user = SimpleNamespace(id=10)
    state = VoiceState(MagicMock(), guild_id=1)
    state.voice = MagicMock()
    state.stop = AsyncMock()
    cog.voice_states[1] = state
    member = SimpleNamespace(id=10, guild=SimpleNamespace(id=1))

    await cog.on_voice_state_update(member, SimpleNamespace(channel="vc"), SimpleNamespace(channel=None))

    state.stop.assert_awaited_once()
    assert state.exists is False


@pytest.mark.asyncio
async def test_someone_else_leaving_voice_is_ignored():
    cog = _cog()
    cog.bot.user = SimpleNamespace(id=10)
    state = VoiceState(MagicMock(), guild_id=1)
    state.voice = MagicMock()
    state.stop = AsyncMock()
    cog.voice_states[1] = state

    await cog.on_voice_state_update(SimpleNamespace(id=99, guild=SimpleNamespace(id=1)),
                                    SimpleNamespace(channel="vc"), SimpleNamespace(channel=None))

    state.stop.assert_not_awaited()
