"""Reusable button-based pagination helpers.

``EmbedPager`` drives every multi-page embed (help, /forecast, /class and the
prefix forecast).  It used to overwrite each page's footer with "Page n/N",
let anyone in the channel flip somebody else's pages, and leave live-looking
buttons behind when it timed out.
"""

import discord
from discord.ui import Button, View


async def _send_paginated_help(interaction: discord.Interaction, title: str, pages: list):
    current_page = 0
    prev = Button(label="Previous", style=discord.ButtonStyle.primary, custom_id="prev", disabled=True)
    next_b = Button(label="Next", style=discord.ButtonStyle.primary, custom_id="next", disabled=len(pages) == 1)
    view = View(timeout=60)
    view.add_item(prev)
    view.add_item(next_b)
    embed = discord.Embed(title=title, description=pages[current_page], color=0x00FF00)
    await interaction.response.send_message(embed=embed, view=view)

    async def cb(interaction: discord.Interaction):
        nonlocal current_page
        if interaction.data["custom_id"] == "prev":
            current_page -= 1
        else:
            current_page += 1
        embed.description = pages[current_page]
        prev.disabled = current_page == 0
        next_b.disabled = current_page == len(pages) - 1
        await interaction.response.edit_message(embed=embed, view=view)

    prev.callback = cb
    next_b.callback = cb


# WeatherCog originally had its own identical helper named ``_paginate``.
_paginate = _send_paginated_help


class EmbedPager(View):
    """◀ / ▶ through a list of embeds, for the person who asked only."""

    def __init__(self, embeds: list[discord.Embed], *, owner_id: int | None, timeout: float = 180):
        super().__init__(timeout=timeout)
        self.embeds = embeds
        self.owner_id = owner_id
        self.index = 0
        self.message: discord.Message | None = None
        # Each page keeps the footer it was built with; the page number is
        # appended to it rather than replacing it.
        self._footers = [embed.footer.text if embed.footer else None for embed in embeds]
        self._stamp_all()
        self._sync_buttons()

    def _stamp_all(self) -> None:
        total = len(self.embeds)
        for index, embed in enumerate(self.embeds):
            base = self._footers[index]
            page = f"Page {index + 1}/{total}"
            icon = embed.footer.icon_url if embed.footer else None
            embed.set_footer(text=f"{base} • {page}" if base else page, icon_url=icon)

    def _sync_buttons(self) -> None:
        self.previous.disabled = self.index == 0
        self.next.disabled = self.index >= len(self.embeds) - 1
        self.counter.label = f"{self.index + 1} / {len(self.embeds)}"

    @property
    def current(self) -> discord.Embed:
        return self.embeds[self.index]

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if self.owner_id is None or interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "These pages belong to someone else — run the command yourself to browse.", ephemeral=True)
        return False

    async def _turn(self, interaction: discord.Interaction, step: int) -> None:
        self.index = max(0, min(self.index + step, len(self.embeds) - 1))
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.current, view=self)

    @discord.ui.button(emoji="◀️", style=discord.ButtonStyle.secondary)
    async def previous(self, interaction: discord.Interaction, button: Button):
        await self._turn(interaction, -1)

    @discord.ui.button(label="1 / 1", style=discord.ButtonStyle.secondary, disabled=True)
    async def counter(self, interaction: discord.Interaction, button: Button):  # pragma: no cover - disabled
        await interaction.response.defer()

    @discord.ui.button(emoji="▶️", style=discord.ButtonStyle.secondary)
    async def next(self, interaction: discord.Interaction, button: Button):
        await self._turn(interaction, 1)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True
        if self.message is None:
            return
        try:
            await self.message.edit(view=self)
        except discord.HTTPException:
            pass


async def _send_paginated_embeds(interaction: discord.Interaction, embeds: list, *, ephemeral: bool = False):
    """Send ``embeds`` as one paged message in reply to a slash command."""
    if not embeds:
        return
    if len(embeds) == 1:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embeds[0], ephemeral=ephemeral)
        else:
            await interaction.response.send_message(embed=embeds[0], ephemeral=ephemeral)
        return
    view = EmbedPager(embeds, owner_id=interaction.user.id)
    if interaction.response.is_done():
        view.message = await interaction.followup.send(embed=view.current, view=view, ephemeral=ephemeral, wait=True)
    else:
        await interaction.response.send_message(embed=view.current, view=view, ephemeral=ephemeral)
        try:
            view.message = await interaction.original_response()
        except discord.HTTPException:
            view.message = None


async def send_paginated_ctx(ctx, embeds: list):
    """The same pager for prefix (text) commands."""
    if not embeds:
        return
    if len(embeds) == 1:
        await ctx.send(embed=embeds[0])
        return
    view = EmbedPager(embeds, owner_id=ctx.author.id)
    view.message = await ctx.send(embed=view.current, view=view)
