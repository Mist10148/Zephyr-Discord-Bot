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


# ---------------------------------------------------------------------------
# Second audit (M19, M21, M24, M26, M27, M30)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_refused_check_is_not_answered_twice():
    """interaction_check already replied; the error handler added "You can't use that here"."""
    from discord import app_commands

    interaction = _error_interaction(done=True)
    await MusicCog.cog_app_command_error(_cog(), interaction, app_commands.CheckFailure())

    interaction.followup.send.assert_not_awaited()
    interaction.response.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_permissions_is_still_explained():
    from discord import app_commands

    interaction = _error_interaction(done=False)
    await MusicCog.cog_app_command_error(_cog(), interaction, app_commands.MissingPermissions(["manage_guild"]))

    assert "Manage Server" in interaction.response.send_message.await_args.args[0]


def test_a_bad_effect_value_changes_nothing():
    from zephyr.cogs.music import VoiceError, _apply_effects

    state = VoiceState(MagicMock(), guild_id=1)
    with pytest.raises(VoiceError):
        _apply_effects(state, {"nightcore": True, "pitch": 5})
    assert state._nightcore_enabled is False
    assert state._pitch == 1.0

    with pytest.raises(VoiceError):
        _apply_effects(state, {"reset": True, "slowed": True, "bass_boost": 99})
    assert state._slowed_enabled is False


@pytest.mark.asyncio
async def test_load_announces_in_the_channel_it_was_run_from():
    cog = _cog()
    cog._connect = AsyncMock()
    cog._ensure_np_channel = MagicMock()
    seen = {}

    def ensure(guild_id, *, channel_id=None, sticky=False):
        seen.update(channel_id=channel_id, sticky=sticky)
        state = VoiceState(MagicMock(), guild_id=guild_id)
        state.start_player = MagicMock()
        return state

    cog.ensure_voice_state = ensure
    member = SimpleNamespace(id=5, mention="<@5>")
    playlist = {"tracks": [{"title": "a", "url": None, "duration_s": 0, "source": "search"}]}

    added = await cog._enqueue_playlist(SimpleNamespace(id=1), member, "vc", playlist, channel_id=42)

    assert added == 1
    assert seen == {"channel_id": 42, "sticky": True}


@pytest.mark.asyncio
async def test_enqueue_track_reports_a_refusal():
    cog = _cog()
    interaction = MagicMock()
    interaction.user.voice = None
    interaction.followup.send = AsyncMock()

    assert await cog._enqueue_track(interaction, Track(title="x")) is False
    interaction.followup.send.assert_awaited_once()


def _voter(user_id, channel="vc"):
    return SimpleNamespace(id=user_id, voice=SimpleNamespace(channel=channel), roles=[],
                           guild_permissions=SimpleNamespace(manage_guild=False))


def test_skip_vote_rules():
    state = VoiceState(MagicMock(), guild_id=1)
    state.voice = MagicMock()
    state.voice.channel = "vc"
    state._skip_threshold = lambda: 2
    state.skip = MagicMock()
    state.current = Track(title="t", requester_id=1)

    assert state.vote_skip(_voter(9, channel="elsewhere"))[0] == "not_listening"
    assert state.vote_skip(_voter(2))[0] == "voted"
    assert state.vote_skip(_voter(2))[0] == "already_voted"
    state.skip.assert_not_called()
    assert state.vote_skip(_voter(3))[0] == "skipped"
    assert state.vote_skip(_voter(1))[0] == "skipped"   # the requester


def test_only_requester_dj_or_manager_force_skip():
    cog = _cog()
    cog._dj_role_ids = {}
    state = VoiceState(MagicMock(), guild_id=1)
    state.current = Track(title="t", requester_id=1)

    assert cog._may_force_skip(state, _voter(1)) is True
    assert cog._may_force_skip(state, _voter(2)) is False   # a plain listener votes

    manager = _voter(3)
    manager.guild_permissions = SimpleNamespace(manage_guild=True)
    assert cog._may_force_skip(state, manager) is True

    cog._dj_role_ids = {1: "77"}
    dj = _voter(4)
    dj.roles = [SimpleNamespace(id=77)]
    assert cog._may_force_skip(state, dj) is True


def test_a_song_opened_from_a_mix_queues_only_that_song():
    from zephyr.cogs.music import _drop_auto_mix

    mixed = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=RDdQw4w9WgXcQ&start_radio=1&index=2"
    assert _drop_auto_mix(mixed) == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"

    playlist = "https://www.youtube.com/watch?v=dQw4w9WgXcQ&list=PL1234567890"
    assert _drop_auto_mix(playlist) == playlist
    assert _drop_auto_mix("lofi beats") == "lofi beats"


@pytest.mark.asyncio
async def test_an_msearch_pick_queues_without_command_data():
    """A select-menu interaction has no command; Context.from_interaction raised on it."""
    cog = _cog()
    state = VoiceState(MagicMock(), guild_id=1)
    state.start_player = MagicMock()
    seen = {}

    def ensure(guild_id, *, channel_id=None, sticky=False):
        seen.update(guild_id=guild_id, channel_id=channel_id, sticky=sticky)
        return state

    cog.ensure_voice_state = ensure
    cog._connect = AsyncMock()
    interaction = MagicMock()
    interaction.command = None
    interaction.guild.id = 1
    interaction.channel_id = 42
    track = Track(title="picked")

    assert await cog._enqueue_track(interaction, track) is True
    assert seen == {"guild_id": 1, "channel_id": 42, "sticky": True}
    assert list(state.songs) == [track]


@pytest.mark.asyncio
async def test_slash_skip_is_instant_for_a_dj():
    cog = _cog()
    cog._dj_role_ids = {1: "77"}
    state = VoiceState(MagicMock(), guild_id=1)
    state.voice = MagicMock()
    state.voice.channel = "vc"
    state._skip_threshold = lambda: 5
    state.skip = MagicMock()
    state.current = Track(title="t", requester_id=1)
    cog.get_voice_state = lambda ctx, sticky=False: state
    cog._interaction_ctx = AsyncMock()
    dj = _voter(4, channel="somewhere else")
    dj.roles = [SimpleNamespace(id=77)]
    interaction = MagicMock()
    interaction.user = dj
    interaction.response.send_message = AsyncMock()

    await MusicCog.skip.callback(cog, interaction)

    state.skip.assert_called_once()
    assert not state.skip_votes


@pytest.mark.asyncio
async def test_queue_total_does_not_count_the_current_song_twice_under_queue_loop():
    state = _state()
    playing = Track(title="Playing", duration_seconds=100)
    state.current = playing
    state.songs.put_nowait(Track(title="Next", duration_seconds=50))
    state.loop = "queue"          # re-appends the playing track at the back
    cog = MusicCog.__new__(MusicCog)
    cog._interaction_ctx = AsyncMock(return_value=SimpleNamespace())
    cog.get_voice_state = MagicMock(return_value=state)
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

    await MusicCog.queue.callback(cog, interaction, page=1)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert "Total duration: 02:30" in embed.footer.text


@pytest.mark.asyncio
async def test_removing_many_long_titles_still_replies():
    state = _state()
    for index in range(100):
        state.songs.put_nowait(Track(title=f"Song {index} " + "x" * 290))
    cog = MusicCog.__new__(MusicCog)
    cog._gate = AsyncMock(return_value=True)
    cog._interaction_ctx = AsyncMock(return_value=SimpleNamespace())
    cog.get_voice_state = MagicMock(return_value=state)
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

    await MusicCog.remove.callback(cog, interaction, index=1, count=100)

    embed = interaction.response.send_message.await_args.kwargs["embed"]
    assert len(state.songs) == 0
    assert len(embed.description) <= 4096
    assert "…and 90 more" in embed.description


@pytest.mark.asyncio
async def test_a_long_lyrics_title_fits_the_embed():
    cog = MusicCog.__new__(MusicCog)
    interaction = SimpleNamespace(followup=SimpleNamespace(send=AsyncMock()))

    await cog._send_lyrics(interaction, "Artist feat. " * 40 + "- Song", "la la la")

    embed = interaction.followup.send.await_args.kwargs["embed"]
    assert len(embed.title) <= 256


# ---------------------------------------------------------------------------
# Third audit (M41, M42)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_idle_timeout_retires_the_state_before_disconnecting(monkeypatch):
    """A /play during the disconnect must not find this state still alive."""
    monkeypatch.setattr("zephyr.cogs.music.IDLE_POLL_SECONDS", 0.01)
    monkeypatch.setattr("zephyr.cogs.music.IDLE_DISCONNECT_SECONDS", 0.02)
    state = _state()
    alive_during_stop = []

    async def stop(*, cancel_player=True):
        alive_during_stop.append(state.exists)

    state.stop = stop
    task = asyncio.create_task(state.audio_player_task())
    await asyncio.wait_for(task, 2)

    assert alive_during_stop == [False]


@pytest.mark.asyncio
async def test_a_slow_lookup_queues_into_a_fresh_player_if_the_old_one_left():
    cog = _cog()
    old = VoiceState(MagicMock(), guild_id=1, channel_id=7)
    old.exists = False
    fresh = VoiceState(MagicMock(), guild_id=1)
    fresh.start_player = MagicMock()
    cog.ensure_voice_state = MagicMock(return_value=fresh)
    cog._connect = AsyncMock()
    cog._ensure_np_channel = MagicMock()
    guild = SimpleNamespace(id=1)

    live = await cog._live_state(old, guild, "vc", channel_id=None)

    assert live is fresh
    cog.ensure_voice_state.assert_called_once_with(1, channel_id=7, sticky=True)
    cog._connect.assert_awaited_once_with(fresh, guild, "vc")
    fresh.start_player.assert_called_once()


@pytest.mark.asyncio
async def test_a_dashboard_play_past_its_deadline_queues_nothing(monkeypatch):
    import time as _time

    from zephyr.cogs.music import VoiceError
    from zephyr.services import bridge

    cog = _cog()
    state = VoiceState(MagicMock(), guild_id=1)
    state.voice = MagicMock()
    state.voice.is_connected.return_value = True
    state.start_player = MagicMock()
    cog.ensure_voice_state = MagicMock(return_value=state)
    cog._connect = AsyncMock()
    cog._ensure_np_channel = MagicMock()
    member = SimpleNamespace(id=5, mention="<@5>", voice=None)
    cog._authorize = MagicMock(return_value=member)
    cog._resolve_query = AsyncMock(return_value=[Track(title="late")])

    token = bridge.command_deadline.set(_time.time() - 1)
    try:
        with pytest.raises(VoiceError, match="took too long"):
            await cog._bridge_play(SimpleNamespace(id=1), "5", {"query": "slow playlist"})
    finally:
        bridge.command_deadline.reset(token)
    assert len(state.songs) == 0


@pytest.mark.asyncio
async def test_a_dashboard_playlist_load_past_its_deadline_queues_nothing(monkeypatch):
    """A slow voice connect outlasted the dashboard's wait and a retry loaded twice."""
    import time as _time

    from zephyr.cogs.music import VoiceError
    from zephyr.services import bridge

    cog = _cog()
    state = VoiceState(MagicMock(), guild_id=1)
    state.start_player = MagicMock()
    cog.ensure_voice_state = MagicMock(return_value=state)
    cog._connect = AsyncMock()
    cog._ensure_np_channel = MagicMock()
    member = SimpleNamespace(id=5, mention="<@5>", voice=None)
    playlist = {"tracks": [{"title": "a", "url": None, "duration_s": 1, "source": "youtube"}]}

    token = bridge.command_deadline.set(_time.time() - 1)
    try:
        with pytest.raises(VoiceError, match="took too long"):
            await cog._enqueue_playlist(SimpleNamespace(id=1), member, object(), playlist)
    finally:
        bridge.command_deadline.reset(token)
    assert len(state.songs) == 0


@pytest.mark.asyncio
async def test_volume_can_be_set_between_songs():
    """The dashboard allowed it; /volume said "Nothing is being played"."""
    state = VoiceState(MagicMock(), guild_id=1)
    state.voice = MagicMock()
    state.voice.is_connected.return_value = True
    cog = MusicCog.__new__(MusicCog)
    cog._gate = AsyncMock(return_value=True)
    cog._interaction_ctx = AsyncMock(return_value=SimpleNamespace())
    cog.get_voice_state = MagicMock(return_value=state)
    interaction = SimpleNamespace(response=SimpleNamespace(send_message=AsyncMock()))

    await MusicCog.volume.callback(cog, interaction, volume=80)

    assert state.volume == 0.8
    assert "80%" in interaction.response.send_message.await_args.kwargs["embed"].description


@pytest.mark.parametrize("command", ["msearch", "lyrics"])
def test_search_queries_are_bounded(command):
    """A 6,000-character query pushed the error reply past 4,096 characters."""
    parameter = next(p for p in getattr(MusicCog, command).parameters if p.name == "query")
    assert parameter.max_value == 200
