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


@pytest.mark.asyncio
async def test_np_runs_the_now_command(monkeypatch):
    """self.now is an app_commands.Command and is not callable; /np raised TypeError."""
    called = []

    async def fake_now(self, interaction):
        called.append(interaction)

    # Patched before the cog exists: a Cog copies its commands on creation.
    monkeypatch.setattr(MusicCog.now, "_callback", fake_now)
    cog = _cog()
    interaction = MagicMock()
    await MusicCog.np.callback(cog, interaction)

    assert called == [interaction]


def _error_interaction(done):
    interaction = MagicMock()
    interaction.response.is_done.return_value = done
    interaction.response.send_message = AsyncMock()
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_an_unexpected_command_error_is_answered():
    """Only CheckFailure was handled; anything else left "Zephyr is thinking..."."""
    from discord import app_commands

    from zephyr.cogs.music import VoiceError

    interaction = _error_interaction(done=True)
    command = MagicMock()
    error = app_commands.CommandInvokeError(command, VoiceError("Could not join General."))

    await MusicCog.cog_app_command_error(_cog(), interaction, error)

    interaction.followup.send.assert_awaited_once_with("❌ Could not join General.", ephemeral=True)


@pytest.mark.asyncio
async def test_a_bug_gets_a_generic_reply_before_any_response():
    from discord import app_commands

    interaction = _error_interaction(done=False)
    error = app_commands.CommandInvokeError(MagicMock(), RuntimeError("secret internals"))

    await MusicCog.cog_app_command_error(_cog(), interaction, error)

    message = interaction.response.send_message.await_args.args[0]
    assert "secret internals" not in message


@pytest.mark.asyncio
async def test_queue_embed_fits_discord_limits_with_long_youtube_rows():
    """Ten rows of links, long titles and mentions overflowed a 1,024-char field."""
    state = _state()
    state.current = Track(title="Now [Official Video] " * 10, url="https://www.youtube.com/watch?v=aaaaaaaaaaa",
                          duration_seconds=213, requester_mention="<@123456789012345678>")
    state.source = MagicMock()
    for index in range(25):
        state.songs.put_nowait(Track(title=f"Song {index} " + "x" * 120,
                                     url=f"https://www.youtube.com/watch?v={index:011d}",
                                     duration_seconds=3600 + index,
                                     requester_mention="<@123456789012345678>"))
    cog = MusicCog.__new__(MusicCog)
    cog._interaction_ctx = AsyncMock(return_value=SimpleNamespace())
    cog.get_voice_state = MagicMock(return_value=state)
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

    await MusicCog.queue.callback(cog, interaction, page=1)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert len(embed.description) <= 4096
    assert all(len(field.value) <= 1024 for field in embed.fields)
    assert len(embed) <= 6000
    assert "](None)" not in embed.description
    assert embed.description.count("\n`") == 10


def test_switching_queue_loop_mid_track_keeps_the_rotation_right():
    state = VoiceState(MagicMock(), guild_id=1)
    playing, queued = Track(title="Playing"), Track(title="Queued")
    state.current = playing
    state.songs.put_nowait(queued)

    state.loop = "queue"
    assert list(state.songs) == [queued, playing]
    state.loop = "off"
    assert list(state.songs) == [queued]


@pytest.mark.asyncio
async def test_autoplay_turned_on_while_idle_starts_the_radio(monkeypatch):
    monkeypatch.setattr("zephyr.cogs.music.IDLE_POLL_SECONDS", 0.01)
    state = _state()
    state._last_track = Track(title="Seed", url="https://www.youtube.com/watch?v=aaaaaaaaaaa")
    radio = Track(title="Radio")

    async def extend():
        state.songs.put_nowait(radio)
        return 1

    state._extend_with_radio = AsyncMock(side_effect=extend)
    waiter = asyncio.create_task(state._wait_for_track(allow_radio=True, radio_tried=True))
    await asyncio.sleep(0.03)
    assert not waiter.done()
    state._autoplay_enabled = True          # toggled while idle
    await asyncio.sleep(0.03)               # one slice with autoplay off resets the attempt
    assert await asyncio.wait_for(waiter, 1) is radio


@pytest.mark.asyncio
async def test_247_turned_on_while_idle_prevents_the_disconnect(monkeypatch):
    monkeypatch.setattr("zephyr.cogs.music.IDLE_POLL_SECONDS", 0.01)
    monkeypatch.setattr("zephyr.cogs.music.IDLE_DISCONNECT_SECONDS", 0.05)
    state = _state()
    state._247_enabled = True
    waiter = asyncio.create_task(state._wait_for_track(allow_radio=False, radio_tried=False))
    await asyncio.sleep(0.15)
    assert not waiter.done()
    state._247_enabled = False
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(waiter, 1)
