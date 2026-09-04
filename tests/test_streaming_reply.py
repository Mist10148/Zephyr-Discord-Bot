"""Showing a reply as it arrives.

A Gemini answer arrived as one message once generation had finished, so a long
reply meant ten or fifteen seconds of nothing but a typing indicator.

Almost all of this file is about the edit rate limit: editing per chunk would be
several edits a second and gets 429s within moments, so the throttle -- and the
guarantee that the *last* state is never dropped -- is the whole design.
"""

from unittest.mock import AsyncMock, MagicMock

import discord
import pytest

from zephyr.core.streaming import MIN_CHARS_TO_SHOW, PLACEHOLDER, StreamingReply


def _channel():
    channel = MagicMock()
    message = MagicMock()
    message.edit = AsyncMock()
    message.delete = AsyncMock()
    channel.send = AsyncMock(return_value=message)
    return channel, message


def _long(prefix="a", length=None):
    return prefix * (length or MIN_CHARS_TO_SHOW + 10)


class TestThePlaceholder:
    @pytest.mark.asyncio
    async def test_it_posts_one_and_removes_it(self):
        """send_response posts the real answer, so leaving this behind would
        duplicate the reply."""
        channel, message = _channel()
        async with StreamingReply(channel) as preview:
            channel.send.assert_awaited_once_with(PLACEHOLDER)
            assert preview.message is message
        message.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_it_is_removed_even_when_generation_raises(self):
        """Otherwise a "Thinking…" message stays on screen forever, which is a
        worse artefact than no streaming at all."""
        channel, message = _channel()
        with pytest.raises(RuntimeError):
            async with StreamingReply(channel):
                raise RuntimeError("model exploded")
        message.delete.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_a_channel_that_refuses_the_placeholder_still_works(self, caplog):
        """No placeholder means no streaming, but the reply itself is
        unaffected."""
        channel, _ = _channel()
        channel.send = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=403), "no"))

        with caplog.at_level("WARNING", logger="zephyr.core.streaming"):
            async with StreamingReply(channel) as preview:
                await preview.update(_long())
        assert preview.failed is True
        assert "Could not post a streaming placeholder" in caplog.text

    @pytest.mark.asyncio
    async def test_a_deleted_placeholder_does_not_raise(self):
        channel, message = _channel()
        message.delete = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=404), "gone"))
        async with StreamingReply(channel):
            pass


class TestTheThrottle:
    @pytest.mark.asyncio
    async def test_the_first_update_shows_immediately(self):
        channel, message = _channel()
        async with StreamingReply(channel) as preview:
            await preview.update(_long())
            message.edit.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rapid_updates_collapse_into_one_edit(self):
        """This is the point: per-chunk edits would be several a second, and
        Discord allows roughly five per five seconds per message."""
        channel, message = _channel()
        async with StreamingReply(channel, interval=10) as preview:
            for index in range(20):
                await preview.update(_long() + str(index))
            assert message.edit.await_count == 1

    @pytest.mark.asyncio
    async def test_a_later_update_shows_once_the_interval_has_passed(self):
        channel, message = _channel()
        async with StreamingReply(channel, interval=0) as preview:
            await preview.update(_long("a"))
            await preview.update(_long("b"))
            assert message.edit.await_count == 2

    @pytest.mark.asyncio
    async def test_a_short_first_chunk_is_not_worth_showing(self):
        """A two-word placeholder replaced immediately reads as a glitch."""
        channel, message = _channel()
        async with StreamingReply(channel) as preview:
            await preview.update("Hi")
            message.edit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_identical_text_is_not_resent(self):
        channel, message = _channel()
        async with StreamingReply(channel, interval=0) as preview:
            await preview.update(_long())
            await preview.update(_long())
            assert message.edit.await_count == 1

    @pytest.mark.asyncio
    async def test_an_empty_update_does_nothing(self):
        channel, message = _channel()
        async with StreamingReply(channel) as preview:
            await preview.update("")
            message.edit.assert_not_awaited()


class TestLimitsAndFailure:
    @pytest.mark.asyncio
    async def test_it_truncates_to_discords_message_ceiling(self):
        channel, message = _channel()
        async with StreamingReply(channel, interval=0) as preview:
            await preview.update("x" * 5000)
        body = message.edit.await_args.kwargs["content"]
        assert len(body) == 2000
        assert body.endswith("…")

    @pytest.mark.asyncio
    async def test_a_rate_limited_edit_stops_further_attempts(self, caplog):
        """Queueing edits that will also fail turns one 429 into many."""
        channel, message = _channel()
        message.edit = AsyncMock(side_effect=discord.HTTPException(MagicMock(status=429), "slow down"))

        with caplog.at_level("INFO", logger="zephyr.core.streaming"):
            async with StreamingReply(channel, interval=0) as preview:
                await preview.update(_long("a"))
                await preview.update(_long("b"))

        assert message.edit.await_count == 1
        assert preview.failed is True
