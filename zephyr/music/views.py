"""The in-Discord player controls.

Every button routes through the cog's own bridge handler rather than
reimplementing the action, which is what keeps one permission model for the
dashboard and the buttons alike.
"""

import math

import discord
from discord.ui import Button, View

from zephyr.core.logging import get_logger
from zephyr.music.common import VoiceError, YTDLError
from zephyr.utils import embeds
from zephyr.utils.time_utils import _format_timestamp

log = get_logger(__name__)


class NowPlayingView(View):
    """Transport controls under the now-playing embed.

    Every button routes through the cog's bridge handler rather than
    reimplementing the action, so the Discord buttons and the web remote cannot
    drift apart -- including the permission check, which lives in
    ``MusicCog._authorize`` and is applied identically to both.

    ``timeout=None`` because the message outlives any interaction, and the view
    is explicitly disabled when playback ends rather than expiring silently and
    leaving buttons that look live and do nothing.
    """

    def __init__(self, cog: 'MusicCog', guild_id: int):
        super().__init__(timeout=None)
        self.cog = cog
        self.guild_id = int(guild_id)

    async def _run(self, interaction: discord.Interaction, action: str, args: dict | None = None):
        handler = self.cog.bridge_actions().get(action)
        try:
            await handler(interaction.guild, interaction.user.id, args or {})
        except (VoiceError, YTDLError) as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        except Exception as exc:
            log.exception("A now-playing button failed")
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return
        # The refresh loop redraws the embed within a few seconds; acknowledging
        # silently keeps the channel free of one message per button press.
        await interaction.response.defer()

    @discord.ui.button(emoji='⏯️', style=discord.ButtonStyle.secondary)
    async def toggle(self, interaction: discord.Interaction, button: Button):
        state = self.cog.peek_voice_state(self.guild_id)
        paused = bool(state and state.voice and state.voice.is_paused())
        await self._run(interaction, 'player.resume' if paused else 'player.pause')

    @discord.ui.button(emoji='⏭️', style=discord.ButtonStyle.secondary)
    async def skip(self, interaction: discord.Interaction, button: Button):
        await self._run(interaction, 'player.skip')

    @discord.ui.button(emoji='🔁', style=discord.ButtonStyle.secondary)
    async def loop_mode(self, interaction: discord.Interaction, button: Button):
        state = self.cog.peek_voice_state(self.guild_id)
        order = ['off', 'track', 'queue']
        current = state.loop if state else 'off'
        following = order[(order.index(current) + 1) % len(order)] if current in order else 'off'
        await self._run(interaction, 'player.loop', {'mode': following})

    @discord.ui.button(emoji='🔀', style=discord.ButtonStyle.secondary)
    async def shuffle(self, interaction: discord.Interaction, button: Button):
        await self._run(interaction, 'player.shuffle')

    @discord.ui.button(emoji='⏹️', style=discord.ButtonStyle.danger)
    async def stop_playback(self, interaction: discord.Interaction, button: Button):
        await self._run(interaction, 'player.stop')


class QueueView(View):
    """A paginated, mutable queue in Discord.

    The dashboard could drag-reorder, jump, remove and clear (C1-C5) while
    Discord could only *read* the queue -- yet every action already existed as a
    bridge handler (`_bridge_jump`, `_bridge_remove`, `_bridge_move`,
    `_bridge_clear`). This exposes them, routing through
    ``cog.bridge_actions()`` exactly as NowPlayingView does, so the buttons, the
    web remote and the slash commands cannot drift apart -- including the
    permission check in ``_authorize``.

    Deliberately not built on ``zephyr/utils/pagination.py``: those two helpers
    are fire-and-forget, return neither the view nor the message, and have **no
    ownership check** -- anyone in the channel can page somebody else's list.
    That is tolerable for a static help page and not for a view whose buttons
    remove tracks.

    ``timeout`` is finite here, unlike NowPlayingView: this message is a
    transient answer to one person's ``/queue``, not a persistent now-playing
    display, so expiring and disabling is correct rather than lossy.
    """

    PAGE_SIZE = 10
    TIMEOUT_SECONDS = 180

    def __init__(self, cog: 'MusicCog', guild_id: int, invoker_id: int):
        super().__init__(timeout=self.TIMEOUT_SECONDS)
        self.cog = cog
        self.guild_id = int(guild_id)
        self.invoker_id = int(invoker_id)
        self.page = 0
        self.message: discord.Message | None = None
        self._sync_buttons()

    # ---- state ----------------------------------------------------------

    @property
    def songs(self) -> list:
        state = self.cog.peek_voice_state(self.guild_id)
        return list(state.songs) if state else []

    @property
    def pages(self) -> int:
        return max(1, math.ceil(len(self.songs) / self.PAGE_SIZE))

    def _sync_buttons(self) -> None:
        self.page = max(0, min(self.page, self.pages - 1))
        self.previous.disabled = self.page == 0
        self.next_page.disabled = self.page >= self.pages - 1
        empty = not self.songs
        self.jump.disabled = empty
        self.remove.disabled = empty

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        """Only the person who ran /queue may drive this view.

        The paging buttons are harmless, but Jump and Remove change what
        everybody is hearing -- and a view anyone can press is how one person's
        /queue becomes another person's remote. Driving the *player* still needs
        _authorize on top of this; this is only about whose message it is.
        """
        if interaction.user.id == self.invoker_id:
            return True
        await interaction.response.send_message(
            "That queue belongs to someone else - run `/queue` for your own.", ephemeral=True
        )
        return False

    # ---- rendering ------------------------------------------------------

    def embed(self) -> discord.Embed:
        state = self.cog.peek_voice_state(self.guild_id)
        songs = self.songs
        embed = embeds.info(title='🎶 Music Queue')

        if state and state.is_playing and state.current:
            embed.add_field(
                name='Currently Playing',
                value=(f"[{state.current.title}]({state.current.url})\n"
                       f"Requested by {state.current.requester_mention} • `{state.current.duration}`"),
                inline=False,
            )
            embed.add_field(name='Loop Mode', value=state.loop.capitalize(), inline=True)
            embed.add_field(name='Volume', value=f"{int(state.volume * 100)}%", inline=True)

        start = self.page * self.PAGE_SIZE
        upcoming = songs[start:start + self.PAGE_SIZE]
        if upcoming:
            embed.add_field(
                name=f'Up Next ({len(songs)} total)',
                value='\n'.join(
                    f'`{index + 1}.` [{song.title}]({song.url}) • {song.duration} • {song.requester_mention}'
                    for index, song in enumerate(upcoming, start=start)
                ),
                inline=False,
            )
        else:
            embed.add_field(name='Up Next', value='No more songs in queue.', inline=False)

        total_duration = state.current.duration_seconds if (state and state.is_playing and state.current) else 0
        for song in songs:
            total_duration += song.duration_seconds or 0
        embed.set_footer(
            text=embeds.footer_text(
                f'Page {self.page + 1}/{self.pages} • Total duration: {_format_timestamp(total_duration)}'
            ),
            icon_url=embeds.icon_url(),
        )
        return embed

    async def refresh(self, interaction: discord.Interaction) -> None:
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    async def on_timeout(self) -> None:
        """Disable rather than expire silently.

        A view that times out leaves buttons that look live and do nothing,
        which is the same defect as the dead Play button on the web.
        """
        for child in self.children:
            child.disabled = True
        if self.message is not None:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass

    # ---- actions --------------------------------------------------------

    async def _run(self, interaction: discord.Interaction, action: str, args: dict | None = None) -> bool:
        handler = self.cog.bridge_actions().get(action)
        try:
            await handler(interaction.guild, interaction.user.id, args or {})
        except (VoiceError, YTDLError) as exc:
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return False
        except Exception as exc:
            log.exception("A queue-view button failed")
            await interaction.response.send_message(f'❌ {exc}', ephemeral=True)
            return False
        return True

    @discord.ui.button(emoji='◀', style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: Button):
        self.page -= 1
        await self.refresh(interaction)

    @discord.ui.button(emoji='▶', style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: Button):
        self.page += 1
        await self.refresh(interaction)

    @discord.ui.button(label='Jump', style=discord.ButtonStyle.primary)
    async def jump(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(_QueueIndexModal(self, 'jump'))

    @discord.ui.button(label='Remove', style=discord.ButtonStyle.danger)
    async def remove(self, interaction: discord.Interaction, button: Button):
        await interaction.response.send_modal(_QueueIndexModal(self, 'remove'))


class _QueueIndexModal(discord.ui.Modal):
    """Asks for a track number.

    A modal rather than a select menu: a select is capped at 25 options, and a
    queue is routinely longer than that -- so a select would silently be unable
    to address most of it. The number shown in the embed is the number typed
    here, which is the whole reason the embed is 1-based.
    """

    index = discord.ui.TextInput(label='Track number', placeholder='e.g. 3', max_length=4)

    def __init__(self, view: 'QueueView', action: str):
        super().__init__(title=f'{action.capitalize()} a track')
        self.view_ref = view
        self.action = action

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.index.value).strip()
        if not raw.isdigit():
            await interaction.response.send_message('❌ That is not a track number.', ephemeral=True)
            return
        position = int(raw)
        total = len(self.view_ref.songs)
        if not 1 <= position <= total:
            await interaction.response.send_message(
                f'❌ Pick a number between 1 and {total}.' if total else '❌ The queue is empty.',
                ephemeral=True,
            )
            return

        # The handlers are 0-based; the embed is 1-based because a queue read by
        # a human starts at one.
        action = 'player.jump' if self.action == 'jump' else 'player.remove'
        if not await self.view_ref._run(interaction, action, {'index': position - 1}):
            return

        self.view_ref._sync_buttons()
        await interaction.response.edit_message(embed=self.view_ref.embed(), view=self.view_ref)
