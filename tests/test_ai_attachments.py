"""What the AI can see.

The mention handler read `message.attachments[0]` and nothing else, so:

* replying to somebody's screenshot and asking about it passed **no image** --
  only the replying message was inspected, so the answer was about nothing;
* a message with two attachments saw whichever came first and silently
  discarded the other;
* a .txt attachment *replaced* whatever was typed, so "summarise this" plus a
  file lost the instruction.
"""

from unittest.mock import AsyncMock, MagicMock

import pytest

from zephyr.client import ZephyrBot


def _bot():
    bot = ZephyrBot.__new__(ZephyrBot)
    bot._ai_channel_policies = {}
    return bot


def _attachment(*, filename="x", content_type=None, url="https://cdn.tld/x", data=b""):
    attachment = MagicMock()
    attachment.filename = filename
    attachment.content_type = content_type
    attachment.url = url
    attachment.read = AsyncMock(return_value=data)
    return attachment


def _image(url="https://cdn.tld/shot.png"):
    return _attachment(filename="shot.png", content_type="image/png", url=url)


def _text(body="file body", filename="notes.txt"):
    return _attachment(filename=filename, content_type="text/plain", data=body.encode())


def _message(attachments=(), reference=None):
    message = MagicMock()
    message.attachments = list(attachments)
    message.reference = reference
    message.channel = MagicMock()
    return message


class TestReadingOneMessage:
    @pytest.mark.asyncio
    async def test_it_finds_an_image(self):
        image_url, text = await _bot()._read_attachments(_message([_image()]))
        assert image_url == "https://cdn.tld/shot.png"
        assert text is None

    @pytest.mark.asyncio
    async def test_it_finds_a_text_file(self):
        image_url, text = await _bot()._read_attachments(_message([_text("hello")]))
        assert image_url is None
        assert text == "hello"

    @pytest.mark.asyncio
    async def test_it_finds_both_rather_than_only_the_first(self):
        """`attachments[0]` saw whichever came first and discarded the other."""
        image_url, text = await _bot()._read_attachments(_message([_text("notes"), _image()]))
        assert image_url == "https://cdn.tld/shot.png"
        assert text == "notes"

    @pytest.mark.asyncio
    async def test_markdown_counts_as_text(self):
        _, text = await _bot()._read_attachments(_message([_text("# title", filename="README.md")]))
        assert text == "# title"

    @pytest.mark.asyncio
    async def test_an_unreadable_attachment_does_not_break_the_reply(self, caplog):
        broken = _text()
        broken.read = AsyncMock(side_effect=RuntimeError("cdn timeout"))
        with caplog.at_level("WARNING", logger="zephyr.client"):
            image_url, text = await _bot()._read_attachments(_message([broken]))
        assert (image_url, text) == (None, None)
        assert "Could not read attachment" in caplog.text

    @pytest.mark.asyncio
    async def test_an_unknown_type_is_ignored(self):
        pdf = _attachment(filename="report.pdf", content_type="application/pdf")
        assert await _bot()._read_attachments(_message([pdf])) == (None, None)

    @pytest.mark.asyncio
    async def test_no_attachments_is_not_an_error(self):
        assert await _bot()._read_attachments(_message()) == (None, None)


class TestReadingTheRepliedToMessage:
    @pytest.mark.asyncio
    async def test_a_resolved_reference_is_used_directly(self):
        """The common flow this used to miss entirely: reply to somebody's
        screenshot and ask about it."""
        reference = MagicMock()
        reference.resolved = _message([_image()])
        reference.message_id = 42

        image_url, _ = await _bot()._read_referenced_attachments(_message(reference=reference))
        assert image_url == "https://cdn.tld/shot.png"

    @pytest.mark.asyncio
    async def test_an_unresolved_reference_is_fetched_once(self):
        """`reference.resolved` is only populated when Discord happened to
        include it."""
        reference = MagicMock()
        reference.resolved = None
        reference.message_id = 42

        message = _message(reference=reference)
        message.channel.fetch_message = AsyncMock(return_value=_message([_image()]))

        image_url, _ = await _bot()._read_referenced_attachments(message)
        assert image_url == "https://cdn.tld/shot.png"
        message.channel.fetch_message.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_a_deleted_reference_degrades_quietly(self):
        reference = MagicMock()
        reference.resolved = None
        reference.message_id = 42

        message = _message(reference=reference)
        message.channel.fetch_message = AsyncMock(side_effect=RuntimeError("unknown message"))
        assert await _bot()._read_referenced_attachments(message) == (None, None)

    @pytest.mark.asyncio
    async def test_no_reference_fetches_nothing(self):
        message = _message()
        message.channel.fetch_message = AsyncMock()
        assert await _bot()._read_referenced_attachments(message) == (None, None)
        message.channel.fetch_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_reference_with_no_id_fetches_nothing(self):
        reference = MagicMock()
        reference.resolved = None
        reference.message_id = None

        message = _message(reference=reference)
        message.channel.fetch_message = AsyncMock()
        assert await _bot()._read_referenced_attachments(message) == (None, None)
        message.channel.fetch_message.assert_not_awaited()
