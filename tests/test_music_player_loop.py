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
        self.playing = False

    def play(self, source, *, after):
        self.played.append(source)
        self.playing = True

        def finished(error=None):
            # As discord.py does: the player has stopped before `after` runs.
            self.playing = False
            after(error)

        self.after = finished

    def stop(self):
        self.playing = False

    def is_connected(self):
        return True

    def is_playing(self):
        return self.playing

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


@pytest.mark.asyncio
async def test_a_finished_track_leaves_the_player_idle(monkeypatch):
    """A finished track used to stay in ``current``, so an effect toggle "restarted" it."""
    state = _state()
    state.songs.put_nowait(Track(title="Only"))
    source = MagicMock(stderr_tail="")
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(return_value=source))

    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: state.voice.played)
    state.voice.after(None)                       # the track ends normally
    await _spin(lambda: state.current is None)
    assert state.current is None and state.source is None
    assert not state.is_playing

    YTDLSource.from_track.reset_mock()
    await state.restart_current(preserve_position=True)
    YTDLSource.from_track.assert_not_awaited()     # nothing to restart

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_a_skip_while_the_track_loads_is_honoured(monkeypatch):
    state = _state()
    first, second = Track(title="First"), Track(title="Second")
    state.songs.put_nowait(first)
    state.songs.put_nowait(second)
    loading = asyncio.Event()
    release = asyncio.Event()
    first_source, second_source = MagicMock(stderr_tail=""), MagicMock(stderr_tail="")

    async def slow_from_track(track, **kwargs):
        if track is first:
            loading.set()
            await release.wait()
            return first_source
        return second_source

    monkeypatch.setattr(YTDLSource, "from_track", slow_from_track)
    task = asyncio.create_task(state.audio_player_task())
    await loading.wait()
    state.skip()                                   # nothing audible yet
    release.set()
    await _spin(lambda: state.voice.played)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    first_source.cleanup.assert_called()
    assert state.voice.played == [second_source]
    assert state.current is second


@pytest.mark.asyncio
async def test_loop_track_replays_after_the_track_ends(monkeypatch):
    state = _state()
    state.loop = "track"
    song = Track(title="Again")
    state.songs.put_nowait(song)
    sources = [MagicMock(stderr_tail=""), MagicMock(stderr_tail="")]
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(side_effect=sources))
    # Do not trip the early-end failure heuristic.
    monkeypatch.setattr("zephyr.cogs.music.MIN_TRACK_SECONDS_FOR_EARLY_END", 10 ** 9)

    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: len(state.voice.played) == 1)
    state.voice.after(None)
    await _spin(lambda: len(state.voice.played) == 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.voice.played == sources
    assert state.current is song


# ---------------------------------------------------------------------------
# Second audit (M18, M22, M23, M25, M28)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_loop_track_stops_replaying_a_track_that_no_longer_loads(monkeypatch):
    state = _state()
    state.loop = "track"
    song, nxt = Track(title="Goes private"), Track(title="Next")
    state.songs.put_nowait(song)
    first, good = MagicMock(stderr_tail=""), MagicMock(stderr_tail="")
    factory = AsyncMock(side_effect=[first, RuntimeError("Video unavailable"), good])
    monkeypatch.setattr(YTDLSource, "from_track", factory)
    monkeypatch.setattr("zephyr.cogs.music.MIN_TRACK_SECONDS_FOR_EARLY_END", 10 ** 9)

    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: len(state.voice.played) == 1)
    state.songs.put_nowait(nxt)
    state.voice.after(None)
    await _spin(lambda: len(state.voice.played) == 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # The failed replay is given up on; the loop moves to the queue instead of
    # re-extracting the dead track forever.
    assert factory.await_count == 3
    assert state.voice.played == [first, good]
    assert state.current is nxt


@pytest.mark.asyncio
async def test_an_effect_toggled_during_the_load_is_applied_with_a_matching_clock(monkeypatch):
    state = _state()
    song = Track(title="Song")
    state.songs.put_nowait(song)
    stale, fresh = MagicMock(stderr_tail=""), MagicMock(stderr_tail="")

    async def load(track, **kwargs):
        if load.calls == 0:
            # /nightcore lands while the first extraction is still running.
            state._nightcore_enabled = True
        load.calls += 1
        return stale if load.calls == 1 else fresh
    load.calls = 0
    monkeypatch.setattr(YTDLSource, "from_track", load)

    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: state.voice.played)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    stale.cleanup.assert_called()
    assert state.voice.played == [fresh]
    assert state._current_rate == state.playback_rate() != 1.0


@pytest.mark.asyncio
async def test_a_seek_during_the_load_is_kept_and_applied(monkeypatch):
    state = _state()
    song = Track(title="Song", duration_seconds=300)
    state.songs.put_nowait(song)
    stale, fresh = MagicMock(stderr_tail=""), MagicMock(stderr_tail="")
    seen_options = []

    async def load(track, *, ffmpeg_options, **kwargs):
        seen_options.append(ffmpeg_options)
        if len(seen_options) == 1:
            applied = await state.restart_current(position=120)
            assert applied is False
        return stale if len(seen_options) == 1 else fresh
    monkeypatch.setattr(YTDLSource, "from_track", load)

    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: state.voice.played)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.voice.played == [fresh]
    assert "-ss 120" in seen_options[1]["before_options"]
    assert state._current_position == 120


@pytest.mark.asyncio
async def test_restart_uses_the_explicit_position_not_a_shared_field(monkeypatch):
    state = _state()
    song = Track(title="Song", duration_seconds=300)
    state.current = song
    state.source = MagicMock()
    state._current_position, state._current_start_time = 10.0, None
    seen = []

    async def load(track, *, ffmpeg_options, **kwargs):
        # Another writer clobbers the shared field mid-restart.
        state._current_position = 10.0
        seen.append(ffmpeg_options["before_options"])
        return MagicMock(stderr_tail="")
    monkeypatch.setattr(YTDLSource, "from_track", load)

    assert await state.restart_current(position=180) is True
    assert "-ss 180" in seen[0]
    assert state._current_position == 180


def test_stream_retry_is_remembered_on_the_track_itself():
    state_attrs = VoiceState.__init__.__code__.co_names
    assert "_stream_retry_ids" not in state_attrs
    track = Track(title="t")
    assert track.stream_retried is False
    # Not part of equality: two queue entries for the same song still compare equal.
    other = Track(title="t")
    other.stream_retried = True
    assert track == other


@pytest.mark.asyncio
async def test_music_waits_for_a_tts_clip_instead_of_failing(monkeypatch):
    state = _state()
    state.voice.playing = True   # /say is speaking

    async def finish_clip():
        await asyncio.sleep(0.3)
        state.voice.playing = False

    clip = asyncio.create_task(finish_clip())
    await asyncio.wait_for(state._wait_for_foreign_audio(timeout=5), timeout=5)
    await clip
    assert state.voice.playing is False


@pytest.mark.asyncio
async def test_a_skip_sent_while_a_tts_clip_plays_is_honoured(monkeypatch):
    """The skip check ran before the wait for /say, so a skip during the clip was lost."""
    state = _state()
    doomed, after = Track(title="Doomed"), Track(title="After")
    state.songs.put_nowait(doomed)
    state.songs.put_nowait(after)
    first, second = MagicMock(name="doomed"), MagicMock(name="after")
    monkeypatch.setattr(YTDLSource, "from_track", AsyncMock(side_effect=[first, second]))
    waits = []

    async def clip_then_skip(timeout=15.0):
        waits.append(state.current)
        if len(waits) == 1:
            state.skip()        # someone runs /skip while /say is talking

    state._wait_for_foreign_audio = clip_then_skip
    task = asyncio.create_task(state.audio_player_task())
    await _spin(lambda: state.voice.played)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert state.voice.played == [second]
    first.cleanup.assert_called()
