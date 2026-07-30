"""The now-playing message: its buttons and its lifecycle.

The view is testable without a gateway because every button delegates to a
bridge handler -- which is the point of routing them that way.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from zephyr.cogs.music import MusicCog, NowPlayingView, Track, VoiceError, VoiceState


def _cog(state=None, actions=None):
    cog = MusicCog.__new__(MusicCog)
    cog.bot = MagicMock()
    cog.voice_states = {1: state} if state else {}
    cog._dj_role_ids = {}
    cog._voice_connect_locks = {}
    if actions is not None:
        cog.bridge_actions = lambda: actions
    return cog


def _state(playing=True, paused=False, loop_mode="off"):
    state = VoiceState(MagicMock(), 1, channel_id=2)
    state.voice = MagicMock()
    state.voice.is_connected.return_value = True
    state.voice.is_playing.return_value = playing
    state.voice.is_paused.return_value = paused
    state.loop = loop_mode
    state.current = Track(title="Song", url="https://youtu.be/dQw4w9WgXcQ", duration_seconds=200)
    return state


def _interaction():
    interaction = MagicMock()
    interaction.guild = MagicMock(id=1)
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    interaction.response.send_message = AsyncMock()
    return interaction


class TestButtons:
    @pytest.mark.asyncio
    async def test_the_toggle_pauses_when_playing_and_resumes_when_paused(self):
        called = []
        actions = {
            "player.pause": AsyncMock(side_effect=lambda *a: called.append("pause")),
            "player.resume": AsyncMock(side_effect=lambda *a: called.append("resume")),
        }
        state = _state(paused=False)
        view = NowPlayingView(_cog(state, actions), 1)

        await view.toggle.callback(_interaction())
        state.voice.is_paused.return_value = True
        await view.toggle.callback(_interaction())

        assert called == ["pause", "resume"]

    @pytest.mark.asyncio
    async def test_the_loop_button_cycles_off_track_queue(self):
        seen = []
        actions = {"player.loop": AsyncMock(side_effect=lambda g, a, args: seen.append(args["mode"]))}
        state = _state(loop_mode="off")
        view = NowPlayingView(_cog(state, actions), 1)

        await view.loop_mode.callback(_interaction())
        state.loop = "track"
        await view.loop_mode.callback(_interaction())
        state.loop = "queue"
        await view.loop_mode.callback(_interaction())

        assert seen == ["track", "queue", "off"]

    @pytest.mark.asyncio
    async def test_a_refused_press_answers_privately_and_changes_nothing(self):
        """The permission check is the bridge handler's, so the button and the
        web remote cannot disagree about who may press it."""
        actions = {"player.skip": AsyncMock(side_effect=VoiceError("You need the DJ role."))}
        view = NowPlayingView(_cog(_state(), actions), 1)
        interaction = _interaction()

        await view.skip.callback(interaction)

        interaction.response.send_message.assert_awaited_once()
        assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True
        assert "DJ role" in interaction.response.send_message.await_args.args[0]
        interaction.response.defer.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_successful_press_is_acknowledged_without_a_new_message(self):
        actions = {"player.skip": AsyncMock(return_value={"skipped": "Song"})}
        view = NowPlayingView(_cog(_state(), actions), 1)
        interaction = _interaction()

        await view.skip.callback(interaction)

        interaction.response.defer.assert_awaited_once()
        interaction.response.send_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_unexpected_failure_still_answers_the_interaction(self):
        """An unanswered interaction shows the user "this failed" with no reason."""
        actions = {"player.skip": AsyncMock(side_effect=RuntimeError("boom"))}
        view = NowPlayingView(_cog(_state(), actions), 1)
        interaction = _interaction()

        await view.skip.callback(interaction)

        interaction.response.send_message.assert_awaited_once()

    def test_the_view_never_times_out(self):
        """It outlives every interaction; expiry would leave live-looking buttons."""
        assert NowPlayingView(_cog(), 1).timeout is None


class TestMessageLifecycle:
    @pytest.mark.asyncio
    async def test_a_new_track_replaces_the_previous_message(self):
        state = _state()
        old = MagicMock(delete=AsyncMock())
        state.np_message = old
        new = MagicMock()
        state._notify = AsyncMock(return_value=new)

        await state.announce_now_playing(state.current)

        old.delete.assert_awaited_once()
        assert state.np_message is new

    @pytest.mark.asyncio
    async def test_retiring_disables_the_buttons_rather_than_deleting(self):
        """The last message of a session stays as a record -- but not a live one."""
        state = _state()
        state.np_message = MagicMock(edit=AsyncMock(), delete=AsyncMock())
        message = state.np_message

        await state.retire_now_playing()

        message.edit.assert_awaited_once_with(view=None)
        message.delete.assert_not_awaited()
        assert state.np_message is None

    @pytest.mark.asyncio
    async def test_a_deleted_message_is_forgotten_rather_than_retried(self):
        state = _state()
        state.np_message = MagicMock(
            edit=AsyncMock(side_effect=discord.NotFound(MagicMock(status=404), "gone"))
        )

        await state.refresh_now_playing()

        assert state.np_message is None

    @pytest.mark.asyncio
    async def test_the_refresh_draws_the_current_position(self):
        state = _state()
        state._current_position = 42.0
        state._current_start_time = None
        state.np_message = MagicMock(edit=AsyncMock())

        await state.refresh_now_playing()

        embed = state.np_message.edit.await_args.kwargs["embed"]
        progress = next(field for field in embed.fields if field.name == "Progress")
        assert "0:42" in progress.value

    @pytest.mark.asyncio
    async def test_stopping_retires_the_message(self):
        state = _state()
        state.np_message = MagicMock(edit=AsyncMock())
        state.voice.disconnect = AsyncMock()
        message = state.np_message

        await state.stop(cancel_player=False)

        message.edit.assert_awaited_once_with(view=None)

    @pytest.mark.asyncio
    async def test_the_refresh_loop_skips_paused_and_idle_players(self):
        """A paused bar is not moving, so an edit would rewrite identical content."""
        playing, paused = _state(playing=True), _state(playing=False, paused=True)
        for state in (playing, paused):
            state.np_message = MagicMock()
            state.refresh_now_playing = AsyncMock()

        cog = _cog()
        cog.voice_states = {1: playing, 2: paused}
        with patch.object(MusicCog, "peek_voice_state", lambda self, gid: cog.voice_states[gid]):
            await MusicCog._now_playing_loop.coro(cog)

        playing.refresh_now_playing.assert_awaited_once()
        paused.refresh_now_playing.assert_not_awaited()
