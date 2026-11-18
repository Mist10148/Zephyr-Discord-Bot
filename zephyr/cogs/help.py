"""Help commands: /help, /helpweather, /helpmusic, /helpchat.

All help pages are rendered from the centralized registry in
``zephyr.utils.help_data`` so categories, ordering, and descriptions stay
consistent across every help command.
"""

import discord
from discord import app_commands
from discord.ext import commands

from zephyr.utils.help_data import (
    CHAT_KEYS,
    HELP_CATEGORIES,
    MUSIC_KEYS,
    WEATHER_KEYS,
    _send_categorized_help,
    categories_by_key,
)

HELP_COLOR = discord.Color.from_rgb(88, 101, 242)
WEATHER_COLOR = discord.Color.from_rgb(56, 189, 248)
MUSIC_COLOR = discord.Color.from_rgb(168, 85, 247)
CHAT_COLOR = discord.Color.from_rgb(245, 165, 36)


class HelpCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="help", description="Show all available commands.")
    async def help_command(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            HELP_CATEGORIES,
            title="Zephyr Commands",
            color=HELP_COLOR,
            include_toc=True,
        )

    @app_commands.command(name="helpweather", description="Show weather-related commands.")
    async def helpweather(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            categories_by_key(*WEATHER_KEYS),
            title="Weather Commands",
            color=WEATHER_COLOR,
        )

    @app_commands.command(name="helpmusic", description="Show music-related commands.")
    async def helpmusic(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            categories_by_key(*MUSIC_KEYS),
            title="Music Commands",
            color=MUSIC_COLOR,
        )

    @app_commands.command(name="helpchat", description="Show AI chat and text-to-speech commands.")
    async def helpchat(self, interaction: discord.Interaction):
        await _send_categorized_help(
            interaction,
            categories_by_key(*CHAT_KEYS),
            title="AI & Voice Commands",
            color=CHAT_COLOR,
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(HelpCog(bot))
