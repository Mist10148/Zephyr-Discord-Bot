"""Reusable button-based pagination helpers.

From the original bot.py: ``_send_paginated_help`` (2270-2292),
``_send_paginated_embeds`` (2312-2336), and the ``WeatherCog._paginate`` method
(769-791, which is identical to ``_send_paginated_help`` and reused here).
"""

import discord
from discord.ui import Button, View

# Aliased: `_send_paginated_embeds` takes a parameter called `embeds`, and an
# unaliased import would be shadowed inside it.
from zephyr.utils import embeds as embed_factory


async def _send_paginated_help(interaction: discord.Interaction, title: str, pages: list):
    current_page = 0
    prev = Button(label="Previous", style=discord.ButtonStyle.primary, custom_id="prev", disabled=True)
    next_b = Button(label="Next", style=discord.ButtonStyle.primary, custom_id="next", disabled=len(pages) == 1)
    view = View(timeout=60)
    view.add_item(prev)
    view.add_item(next_b)
    embed = embed_factory.info(pages[current_page], title=title)
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


def _stamp(embed: discord.Embed, index: int, total: int) -> None:
    """Put "Page 2/5" on an embed without losing the bot's identity.

    A bare `set_footer` here replaced whatever the factory put there, so every
    paginated reply in the bot silently lost the shared footer and icon -- which
    is the one place a per-page stamp and a global footer collide.
    """
    embed.set_footer(
        text=embed_factory.footer_text(f"Page {index + 1}/{total}"),
        icon_url=embed_factory.icon_url(),
    )


async def _send_paginated_embeds(interaction: discord.Interaction, embeds: list):
    if not embeds:
        return
    current_page = 0
    prev = Button(label="◀ Previous", style=discord.ButtonStyle.primary, custom_id="prev", disabled=True)
    next_b = Button(label="Next ▶", style=discord.ButtonStyle.primary, custom_id="next", disabled=len(embeds) == 1)
    view = View(timeout=120)
    view.add_item(prev)
    view.add_item(next_b)
    _stamp(embeds[current_page], current_page, len(embeds))
    if interaction.response.is_done():
        await interaction.followup.send(embed=embeds[current_page], view=view)
    else:
        await interaction.response.send_message(embed=embeds[current_page], view=view)

    async def cb(interaction: discord.Interaction):
        nonlocal current_page
        if interaction.data["custom_id"] == "prev":
            current_page -= 1
        else:
            current_page += 1
        _stamp(embeds[current_page], current_page, len(embeds))
        prev.disabled = current_page == 0
        next_b.disabled = current_page == len(embeds) - 1
        await interaction.response.edit_message(embed=embeds[current_page], view=view)

    prev.callback = cb
    next_b.callback = cb
