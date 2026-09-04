"""The interactive queue in Discord.

The dashboard could reorder, jump, remove and clear the queue (C1-C5) while
Discord could only *read* it -- yet every action already existed as a bridge
handler. This is about exposing them safely: paging, an ownership check, and a
1-based index that matches what the embed shows.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from zephyr.cogs.music import MusicCog, QueueView, VoiceError, _QueueIndexModal


def _track(title):
    track = MagicMock()
    track.title = title
    track.url = f"https://y.tld/{title}"
    track.duration = "3:30"
    track.duration_seconds = 210
    track.requester_mention = "<@1>"
    return track


def _cog(count=0, playing=False):
    cog = MusicCog.__new__(MusicCog)
    state = MagicMock()
    state.songs = [_track(f"t{index}") for index in range(count)]
    state.is_playing = playing
    state.current = _track("now") if playing else None
    state.loop = "off"
    state.volume = 0.5
    cog.peek_voice_state = MagicMock(return_value=state)
    cog.bridge_actions = MagicMock(return_value={
        "player.jump": AsyncMock(),
        "player.remove": AsyncMock(),
    })
    return cog


def _interaction(user_id=1):
    interaction = MagicMock()
    interaction.user = MagicMock()
    interaction.user.id = user_id
    interaction.guild = MagicMock()
    interaction.guild.id = 7
    interaction.response = MagicMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_modal = AsyncMock()
    return interaction


class TestPaging:
    def test_the_first_page_cannot_go_back(self):
        view = QueueView(_cog(25), 7, 1)
        assert view.previous.disabled is True
        assert view.next_page.disabled is False

    def test_the_last_page_cannot_go_forward(self):
        view = QueueView(_cog(25), 7, 1)
        view.page = 2
        view._sync_buttons()
        assert view.next_page.disabled is True

    def test_a_short_queue_has_one_page(self):
        view = QueueView(_cog(4), 7, 1)
        assert view.pages == 1
        assert view.previous.disabled and view.next_page.disabled

    def test_an_empty_queue_disables_the_mutating_buttons(self):
        """A control that does nothing must not exist -- and Jump on an empty
        queue is exactly that."""
        view = QueueView(_cog(0), 7, 1)
        assert view.jump.disabled is True
        assert view.remove.disabled is True

    def test_a_page_beyond_the_end_is_clamped(self):
        """The queue shrinks under the view as tracks play, so the stored page
        can outlive the page it points at."""
        view = QueueView(_cog(25), 7, 1)
        view.page = 9
        view._sync_buttons()
        assert view.page == view.pages - 1

    @pytest.mark.asyncio
    async def test_turning_a_page_edits_rather_than_posting(self):
        """One message, not one per press."""
        view = QueueView(_cog(25), 7, 1)
        view.page = 1
        interaction = _interaction()
        await view.previous.callback(interaction)
        interaction.response.edit_message.assert_awaited_once()
        assert view.page == 0


class TestOwnership:
    @pytest.mark.asyncio
    async def test_the_invoker_may_drive_it(self):
        view = QueueView(_cog(5), 7, invoker_id=1)
        assert await view.interaction_check(_interaction(user_id=1)) is True

    @pytest.mark.asyncio
    async def test_nobody_else_may(self):
        """Jump and Remove change what everybody is hearing, so a view anyone
        can press is how one person's /queue becomes another person's remote.
        zephyr/utils/pagination.py has no such check, which is why this view is
        not built on it."""
        view = QueueView(_cog(5), 7, invoker_id=1)
        interaction = _interaction(user_id=2)
        assert await view.interaction_check(interaction) is False
        assert "belongs to someone else" in interaction.response.send_message.await_args.args[0]


class TestTheEmbed:
    def test_it_numbers_tracks_from_one(self):
        """The number in the embed is the number typed into the modal, which is
        why it is 1-based even though the handlers are 0-based."""
        view = QueueView(_cog(3), 7, 1)
        text = view.embed().fields[0].value
        assert "`1.`" in text and "`3.`" in text
        assert "`0.`" not in text

    def test_the_second_page_continues_the_numbering(self):
        view = QueueView(_cog(15), 7, 1)
        view.page = 1
        assert "`11.`" in view.embed().fields[0].value

    def test_it_shows_the_current_track_when_playing(self):
        embed = QueueView(_cog(2, playing=True), 7, 1).embed()
        assert embed.fields[0].name == "Currently Playing"

    def test_the_footer_carries_the_page_and_the_total_duration(self):
        embed = QueueView(_cog(15), 7, 1).embed()
        assert "Page 1/2" in embed.footer.text
        assert "Total duration" in embed.footer.text

    def test_an_empty_queue_says_so_rather_than_rendering_nothing(self):
        embed = QueueView(_cog(0), 7, 1).embed()
        assert "No more songs" in embed.fields[0].value


class TestTheIndexModal:
    @pytest.mark.asyncio
    async def test_it_converts_to_the_zero_based_handler(self):
        cog = _cog(5)
        view = QueueView(cog, 7, 1)
        modal = _QueueIndexModal(view, "remove")
        modal.index = MagicMock(value="3")

        await modal.on_submit(_interaction())
        cog.bridge_actions()["player.remove"].assert_awaited_once()
        args = cog.bridge_actions()["player.remove"].await_args.args
        assert args[2] == {"index": 2}

    @pytest.mark.asyncio
    async def test_jump_and_remove_reach_different_handlers(self):
        cog = _cog(5)
        view = QueueView(cog, 7, 1)
        modal = _QueueIndexModal(view, "jump")
        modal.index = MagicMock(value="1")

        await modal.on_submit(_interaction())
        cog.bridge_actions()["player.jump"].assert_awaited_once()
        cog.bridge_actions()["player.remove"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_non_number_is_refused_readably(self):
        view = QueueView(_cog(5), 7, 1)
        modal = _QueueIndexModal(view, "remove")
        modal.index = MagicMock(value="third one")

        interaction = _interaction()
        await modal.on_submit(interaction)
        assert "not a track number" in interaction.response.send_message.await_args.args[0]

    @pytest.mark.asyncio
    async def test_an_out_of_range_number_names_the_range(self):
        view = QueueView(_cog(5), 7, 1)
        modal = _QueueIndexModal(view, "remove")
        modal.index = MagicMock(value="99")

        interaction = _interaction()
        await modal.on_submit(interaction)
        assert "between 1 and 5" in interaction.response.send_message.await_args.args[0]

    @pytest.mark.asyncio
    async def test_zero_is_out_of_range(self):
        """A 1-based embed means 0 is not a track, and passing it through would
        reach index -1 -- the last track."""
        view = QueueView(_cog(5), 7, 1)
        modal = _QueueIndexModal(view, "remove")
        modal.index = MagicMock(value="0")

        interaction = _interaction()
        await modal.on_submit(interaction)
        assert "between 1 and 5" in interaction.response.send_message.await_args.args[0]
        view.cog.bridge_actions()["player.remove"].assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_refused_action_reports_the_reason_and_stops(self):
        """_authorize raises VoiceError, and its message is written for users."""
        cog = _cog(5)
        cog.bridge_actions.return_value["player.remove"] = AsyncMock(
            side_effect=VoiceError("You need the DJ role to do that.")
        )
        view = QueueView(cog, 7, 1)
        modal = _QueueIndexModal(view, "remove")
        modal.index = MagicMock(value="1")

        interaction = _interaction()
        await modal.on_submit(interaction)
        assert "DJ role" in interaction.response.send_message.await_args.args[0]
        interaction.response.edit_message.assert_not_awaited()


class TestTimeout:
    @pytest.mark.asyncio
    async def test_it_disables_rather_than_expiring_silently(self):
        """A timed-out view leaves buttons that look live and do nothing --
        the same defect as the dead Play button on the web."""
        view = QueueView(_cog(5), 7, 1)
        view.message = MagicMock()
        view.message.edit = AsyncMock()

        await view.on_timeout()
        assert all(child.disabled for child in view.children)
        view.message.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_deleted_message_does_not_raise(self):
        view = QueueView(_cog(5), 7, 1)
        view.message = MagicMock()
        view.message.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404), "gone"))
        await view.on_timeout()

    @pytest.mark.asyncio
    async def test_no_message_yet_does_not_raise(self):
        await QueueView(_cog(5), 7, 1).on_timeout()
