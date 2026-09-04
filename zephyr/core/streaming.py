"""Show a reply as it arrives, instead of after it finishes.

A Gemini answer of any length arrived as one message once generation had
finished. The typing indicator covered the silence but said nothing about
progress, and a long reply meant ten or fifteen seconds of nothing.

The whole difficulty is Discord's edit rate limit. Editing per chunk would be
several edits per second and gets 429s within moments, so this throttles to one
edit per ``EDIT_INTERVAL_SECONDS`` and always performs a final edit with the
complete text -- the throttle can drop intermediate states but never the last
one.

Deliberately not a second sending path. ``send_response`` still produces the
final message in whichever format the context is configured for, so the three
output formats, the chunking and the file fallback all stay in one place. This
only replaces the *waiting*.
"""

from __future__ import annotations

import time

import discord

from zephyr.core.logging import get_logger

log = get_logger(__name__)

# Discord allows roughly five edits per five seconds per message. 1.5s leaves
# headroom for the final edit and for anything else the bot is doing.
EDIT_INTERVAL_SECONDS = 1.5
# Below this there is nothing worth showing, and a two-word placeholder that
# gets replaced immediately reads as a glitch.
MIN_CHARS_TO_SHOW = 40
# Discord's own ceiling for a message body.
MAX_MESSAGE_CHARS = 2000
PLACEHOLDER = "*Thinking…*"


class StreamingReply:
    """A single message, edited as text arrives.

    Used as an async context manager so the placeholder is always cleaned up:
    if generation raises, the "Thinking…" message is deleted rather than left
    on screen forever, which would be a worse artefact than no streaming at all.
    """

    def __init__(self, channel, *, interval: float = EDIT_INTERVAL_SECONDS):
        self.channel = channel
        self.interval = interval
        self.message: discord.Message | None = None
        self._last_edit = 0.0
        self._last_shown = ""
        self.failed = False

    async def __aenter__(self) -> "StreamingReply":
        try:
            self.message = await self.channel.send(PLACEHOLDER)
        except discord.HTTPException:
            # No placeholder, no streaming -- but the reply itself still works.
            log.warning("Could not post a streaming placeholder", exc_info=True)
            self.failed = True
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        # Always removed. send_response posts the real answer, so leaving this
        # behind would duplicate the reply on success and leave a dangling
        # "Thinking…" on failure.
        await self.discard()
        return False

    async def update(self, text: str) -> None:
        """Show ``text``, at most once per interval."""
        if self.failed or self.message is None or not text:
            return
        if len(text) < MIN_CHARS_TO_SHOW:
            return
        now = time.monotonic()
        if now - self._last_edit < self.interval:
            return
        await self._edit(text, now)

    async def _edit(self, text: str, now: float) -> None:
        body = text if len(text) <= MAX_MESSAGE_CHARS else text[: MAX_MESSAGE_CHARS - 1] + "…"
        if body == self._last_shown:
            return
        try:
            await self.message.edit(content=body)
        except discord.HTTPException:
            # Rate-limited, or the message was deleted. Stop trying rather than
            # queueing edits that will also fail.
            log.info("Stopping a streaming reply after an edit failure", exc_info=True)
            self.failed = True
            return
        self._last_shown = body
        self._last_edit = now

    async def discard(self) -> None:
        if self.message is None:
            return
        try:
            await self.message.delete()
        except discord.HTTPException:
            pass
        finally:
            self.message = None
